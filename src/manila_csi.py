# Copyright 2026 vlad.apostol@canonical.com
# See LICENSE file for licensing details.

"""Manila CSI workload manager for deploying and managing CSI drivers."""

import base64
import configparser
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from lightkube import Client, KubeConfig
from lightkube.core.exceptions import ApiError, ConfigError
from lightkube.resources.core_v1 import Secret

logger = logging.getLogger(__name__)


class ManilaCsiManager:
    """Manager for Manila CSI and NFS CSI driver deployments."""

    MANILA_SECRET_NAME = "openstack-manila-secret"

    def __init__(self, charm_dir: Path, app_name: str, model_name: str):
        """Initialize the Manila CSI manager.

        Args:
            charm_dir: Path to the charm directory
            app_name: Name of the charm application
            model_name: Name of the Juju model
        """
        self.charm_dir = charm_dir
        self.app_name = app_name
        self.model_name = model_name
        self.charts_dir = charm_dir / "charts"
        self.manifests_dir = charm_dir / "manifests"
        self._client: Client | None = None

    # Kubeconfig paths for snapped Kubernetes distributions, tried in order
    # when the standard discovery (KUBECONFIG env var / ~/.kube/config) fails.
    _SNAP_KUBECONFIG_PATHS = [
        # canonical k8s snap (k8s)
        Path("/etc/kubernetes/admin.conf"),
        # microk8s snap
        Path("/var/snap/microk8s/current/credentials/client.config"),
    ]

    @property
    def client(self) -> Client:
        """Get or create Kubernetes client.

        Tries standard kubeconfig discovery first (KUBECONFIG env var,
        ~/.kube/config, in-cluster service account), then falls back to
        known snap-based Kubernetes kubeconfig locations.
        """
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self) -> Client:
        """Create a Kubernetes client with snap-aware kubeconfig discovery."""
        try:
            return Client()
        except ConfigError:
            pass

        for path in self._SNAP_KUBECONFIG_PATHS:
            if path.exists():
                logger.info("Using snap kubeconfig at %s", path)
                return Client(KubeConfig.from_file(path).get())

        raise ConfigError(
            "Could not locate a kubeconfig file. Tried standard locations "
            f"and snap paths: {self._SNAP_KUBECONFIG_PATHS}"
        )

    def install(self) -> None:
        """Install Manila CSI components."""
        logger.info("Installing Manila CSI components")
        self._install_helm()
        logger.info("Manila CSI installation prepared")

    def _install_helm(self) -> None:
        """Ensure helm is installed via snap."""
        result = subprocess.run(["which", "helm"], capture_output=True, check=False)
        if result.returncode == 0:
            logger.info("helm already installed at %s", result.stdout.strip())
            return
        logger.info("Installing helm via snap")
        subprocess.run(
            ["snap", "install", "helm", "--classic"],
            check=True,
        )
        logger.info("helm installed successfully")

    def configure(self, config: dict[str, Any]) -> None:
        """Configure and deploy Manila CSI components.

        Args:
            config: Configuration dictionary with deployment settings
        """
        namespace = config["namespace"]

        logger.info("Configuring Manila CSI in namespace %s", namespace)

        # 0. Verify Kubernetes cluster is ready before applying any configuration
        self._wait_for_k8s_ready()

        # 1. Ensure namespace exists
        self._ensure_namespace(namespace)

        # 2. Create openstack-manila-secret from cloud-controller-config
        self._create_cloud_config_secret(
            namespace=namespace,
            source_secret_name=config["cloud_controller_config_secret"],
            source_namespace=config["cloud_controller_config_namespace"],
        )

        # 3. Deploy NFS CSI first — Manila CSI requires it to be present
        if config["deploy_nfs_csi"]:
            logger.info("NFS CSI deployment enabled - deploying NFS CSI driver")
            self._deploy_nfs_csi(config)
        else:
            logger.info("NFS CSI deployment disabled - skipping NFS CSI driver deployment")
            # Validate configuration consistency
            if config["manila_share_protocol"] == "NFS":
                logger.warning(
                    "Manila share protocol is set to 'NFS' but "
                    "NFS CSI deployment is disabled. "
                    "Ensure NFS CSI driver is deployed separately"
                    " or enable 'deploy-nfs-csi' config."
                )

        # 4. Deploy Manila CSI Helm chart
        self._deploy_manila_csi(config)

        # 5. Create storage class
        self._create_storage_class(config)

        # 6. Create VolumeSnapshotClass
        self._create_volume_snapshot_class()

        logger.info("Manila CSI configuration complete")

    def remove(self, config: dict[str, Any]) -> None:
        """Remove Manila CSI components.

        Args:
            config: Configuration dictionary with deployment settings
        """
        logger.info("Removing Manila CSI components")

        namespace = config["namespace"]
        manila_release = config["manila_csi_release"]
        nfs_release = config["nfs_csi_release"]

        # Remove Helm releases
        self._helm_uninstall(manila_release, namespace)

        if config["deploy_nfs_csi"]:
            self._helm_uninstall(nfs_release, namespace)

        # Remove the storage class
        self._delete_storage_class(config["storage_class_name"])

        # Remove the openstack-manila-secret created during configure
        self._delete_secret(self.MANILA_SECRET_NAME, namespace)

        # Remove the VolumeSnapshotClass created during configure
        self._delete_volume_snapshot_class()

        logger.info("Manila CSI components removed")

    def is_ready(self, namespace: str) -> bool:
        """Check if Manila CSI is ready.

        Args:
            namespace: Kubernetes namespace where Manila CSI is deployed

        Returns:
            True if Manila CSI is deployed and ready
        """
        try:
            # Check if Manila CSI pods are running
            result = subprocess.run(
                [
                    "sudo",
                    "k8s",
                    "kubectl",
                    "get",
                    "pods",
                    "-n",
                    namespace,
                    "-l",
                    "release=manila-csi",
                    "-o",
                    "json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                return False

            pods_data = yaml.safe_load(result.stdout)
            if not pods_data.get("items"):
                return False

            # Check if all pods are running
            for pod in pods_data["items"]:
                status = pod.get("status", {}).get("phase")
                if status != "Running":
                    return False

            return True
        except (subprocess.SubprocessError, yaml.YAMLError) as e:
            logger.warning("Failed to check Manila CSI status: %s", e)
            return False

    def _delete_storage_class(self, name: str) -> None:
        """Delete the Manila storage class.

        Args:
            name: Name of the storage class to delete.
        """
        logger.info("Deleting storage class '%s'", name)
        subprocess.run(
            [
                "sudo",
                "k8s",
                "kubectl",
                "delete",
                "storageclass",
                name,
                "--ignore-not-found",
            ],
            capture_output=True,
            check=False,
        )

    def _delete_secret(self, name: str, namespace: str) -> None:
        """Delete a Kubernetes secret.

        Args:
            name: Name of the secret to delete.
            namespace: Namespace the secret lives in.
        """
        logger.info("Deleting secret '%s' from namespace '%s'", name, namespace)
        subprocess.run(
            [
                "sudo",
                "k8s",
                "kubectl",
                "delete",
                "secret",
                name,
                "-n",
                namespace,
                "--ignore-not-found",
            ],
            capture_output=True,
            check=False,
        )

    def _wait_for_k8s_ready(self, timeout: int = 60) -> None:
        """Wait until the Kubernetes API server is reachable and at least one
        node is in Ready condition.

        Args:
            timeout: Maximum seconds to wait before raising RuntimeError.

        Raises:
            RuntimeError: If the cluster is not ready within the timeout.
        """
        logger.info("Checking Kubernetes cluster readiness")
        start_time = time.time()

        while time.time() - start_time < timeout:
            result = subprocess.run(
                ["sudo", "k8s", "kubectl", "get", "nodes", "-o", "json"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                try:
                    nodes_data = yaml.safe_load(result.stdout)
                    nodes = nodes_data.get("items", [])
                    if nodes:
                        ready_nodes = [
                            node
                            for node in nodes
                            if any(
                                cond.get("type") == "Ready" and cond.get("status") == "True"
                                for cond in node.get("status", {}).get("conditions", [])
                            )
                        ]
                        if ready_nodes:
                            logger.info(
                                "Kubernetes cluster ready (%d/%d nodes Ready)",
                                len(ready_nodes),
                                len(nodes),
                            )
                            return
                except yaml.YAMLError as e:
                    logger.debug("Failed to parse node list: %s", e)

            logger.debug("Kubernetes not ready yet, retrying in 10s...")
            time.sleep(10)

        raise RuntimeError(
            f"Kubernetes cluster not ready after {timeout}s. "
            "Ensure the cluster is running and kubeconfig is accessible."
        )

    def _ensure_namespace(self, namespace: str) -> None:
        """Ensure the namespace exists."""
        try:
            subprocess.run(
                ["sudo", "k8s", "kubectl", "create", "namespace", namespace],
                capture_output=True,
                check=False,
            )
            logger.info("Namespace '%s' ensured", namespace)
        except subprocess.SubprocessError as e:
            logger.debug("Namespace creation: %s", e)

    def _create_cloud_config_secret(
        self,
        namespace: str,
        source_secret_name: str,
        source_namespace: str,
    ) -> None:
        """Read the OpenStack cloud-controller-config secret and create/update
        the 'openstack-manila-secret' secret that Manila CSI expects.

        The source secret contains a single key whose value is a b64-encoded
        cloud.conf INI file.  We decode it and store it verbatim under the
        ``cloud.conf`` key in the destination secret so that the Manila CSI
        driver can mount or reference it directly.

        Args:
            namespace: Destination namespace for the openstack-manila-secret.
            source_secret_name: Name of the source secret (cloud-controller-config).  # noqa: E501
            source_namespace: Namespace that contains the source secret.

        Raises:
            RuntimeError: If the source secret is missing or has no usable data.
        """
        dest_secret_name = self.MANILA_SECRET_NAME

        logger.info(
            "Reading '%s' from namespace '%s'",
            source_secret_name,
            source_namespace,
        )

        try:
            source = self.client.get(Secret, name=source_secret_name, namespace=source_namespace)
        except ApiError as e:
            if e.status.code == 404:
                raise RuntimeError(
                    f"Source secret '{source_secret_name}' not found in "
                    f"namespace '{source_namespace}'. Ensure the OpenStack "
                    "cloud-controller-manager has been deployed and has "
                    "created this secret."
                ) from e
            raise

        raw_data: dict = source.data or {}
        if not raw_data:
            raise RuntimeError(
                f"Source secret '{source_secret_name}' exists but contains no data."  # noqa: E501
            )

        # The secret may store values as already-b64-encoded
        # strings (Secret.data)
        # or as plain strings (Secret.stringData).  lightkube exposes .data as
        # the raw b64 values from the API, so we decode each value to get the
        # actual conf content, then re-encode it cleanly for the new secret.
        decoded: dict[str, bytes] = {}
        for key, value in raw_data.items():
            if isinstance(value, str):
                decoded[key] = base64.b64decode(value)
            else:
                decoded[key] = value  # already bytes

        # Use the first (typically only) value as cloud.conf
        conf_bytes = next(iter(decoded.values()))

        # Parse the cloud.conf INI to extract individual OpenStack credentials
        conf_text = conf_bytes.decode("utf-8")
        parser = configparser.ConfigParser()
        parser.read_string(conf_text)

        global_cfg = dict(parser["Global"]) if "Global" in parser else {}

        domain_name = global_cfg.get("user-domain-name", "")

        def b64(s: str) -> str:
            return base64.b64encode(s.encode("utf-8")).decode("ascii")

        # Build the destination secret in the same format as manifests/manila-secret.yaml
        dest_manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {
                "name": dest_secret_name,
                "namespace": namespace,
            },
            "data": {
                "os-authURL": b64(global_cfg.get("auth-url", "")),
                "os-certAuthorityPath": b64(
                    global_cfg.get("ca-file", "/etc/config/endpoint-ca.crt")
                ),
                "os-domainName": b64(domain_name),
                "os-password": b64(global_cfg.get("password", "")),
                "os-userName": b64(global_cfg.get("username", "")),
                "os-projectDomainName": b64(global_cfg.get("tenant-domain-name", domain_name)),
                "os-projectName": b64(global_cfg.get("tenant-name", "")),
                "os-region": b64(global_cfg.get("region", "")),
            },
        }

        result = subprocess.run(
            ["sudo", "k8s", "kubectl", "apply", "-f", "-"],
            input=yaml.dump(dest_manifest),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create '{dest_secret_name}' secret: {result.stderr}")

        logger.info("Secret '%s' created/updated in namespace '%s'", dest_secret_name, namespace)

    def _deploy_manila_csi(self, config: dict[str, Any]) -> None:
        """Deploy Manila CSI by rendering the Helm chart and applying the manifests.

        Args:
            config: Configuration dictionary
        """
        release_name = config["manila_csi_release"]
        namespace = config["namespace"]

        chart_path = config.get("manila_csi_chart_path")
        if chart_path and Path(chart_path).exists():
            logger.info("Using Manila CSI chart from resource: %s", chart_path)
        else:
            chart_path = self._find_local_chart("openstack-manila-csi")
            if chart_path is None:
                raise RuntimeError(
                    f"Manila CSI chart not found in {self.charts_dir} "
                    "(expected 'openstack-manila-csi*.tgz'). "
                    "Please ensure the chart tarball is present in the charts "
                    "directory or attach as a resource."
                )
            logger.info("Using Manila CSI chart from local path: %s", chart_path)

        logger.info("Deploying Manila CSI release '%s'", release_name)
        values_file = self.manifests_dir / "manila-csi-values.yaml"
        self._helm_template_apply(
            release_name,
            str(chart_path),
            namespace,
            values_file=values_file if values_file.exists() else None,
        )
        logger.info("Manila CSI deployed successfully")
        self._wait_for_deployment(namespace, f"release={release_name}")

    def _deploy_nfs_csi(self, config: dict[str, Any]) -> None:
        """Deploy NFS CSI by rendering the Helm chart and applying the manifests.

        Args:
            config: Configuration dictionary
        """
        release_name = config["nfs_csi_release"]
        namespace = config["namespace"]

        chart_path = config.get("nfs_csi_chart_path")
        if chart_path and Path(chart_path).exists():
            logger.info("Using NFS CSI chart from resource: %s", chart_path)
        else:
            chart_path = self._find_local_chart("csi-driver-nfs")
            if chart_path is None:
                raise RuntimeError(
                    f"NFS CSI chart not found in {self.charts_dir} "
                    "(expected 'csi-driver-nfs*.tgz'). "
                    "Please ensure the chart tarball is present in the charts "
                    "directory or attach as a resource."
                )
            logger.info("Using NFS CSI chart from local path: %s", chart_path)

        logger.info("Deploying NFS CSI release '%s'", release_name)
        values_file = self.manifests_dir / "nfs-csi-values.yaml"
        self._helm_template_apply(
            release_name,
            str(chart_path),
            namespace,
            values_file=values_file if values_file.exists() else None,
        )
        logger.info("NFS CSI deployed successfully")
        self._wait_for_deployment(namespace, f"app.kubernetes.io/instance={release_name}")

    def _create_storage_class(self, config: dict[str, Any]) -> None:
        """Create Manila storage class.

        Args:
            config: Configuration dictionary
        """
        storage_class_name = config["storage_class_name"]
        protocol = config["manila_share_protocol"]

        logger.info("Creating storage class '%s'", storage_class_name)

        # Try to load from manifest first
        sc_manifest = self.manifests_dir / "storage-class.yaml"

        if sc_manifest.exists():
            with open(sc_manifest, encoding="utf8") as f:
                sc_data = yaml.safe_load(f)

            # Update with config values
            sc_data["metadata"]["name"] = storage_class_name
            if "parameters" in sc_data:
                sc_data["parameters"]["type"] = protocol
        else:
            # Create default storage class
            sc_data = {
                "apiVersion": "storage.k8s.io/v1",
                "kind": "StorageClass",
                "metadata": {"name": storage_class_name},
                "provisioner": "nfs.manila.csi.openstack.org",
                "parameters": {
                    "type": protocol,
                    "csi.storage.k8s.io/provisioner-secret-name": self.MANILA_SECRET_NAME,
                    "csi.storage.k8s.io/provisioner-secret-namespace": config["namespace"],
                    "csi.storage.k8s.io/node-stage-secret-name": self.MANILA_SECRET_NAME,
                    "csi.storage.k8s.io/node-stage-secret-namespace": config["namespace"],
                    "csi.storage.k8s.io/node-publish-secret-name": self.MANILA_SECRET_NAME,
                    "csi.storage.k8s.io/node-publish-secret-namespace": config["namespace"],
                },
                "allowVolumeExpansion": True,
            }

        try:
            # Apply using kubectl
            result = subprocess.run(
                ["sudo", "k8s", "kubectl", "apply", "-f", "-"],
                input=yaml.dump(sc_data),
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info("Storage class '%s' created", storage_class_name)
            logger.debug("Storage class creation: %s", result.stdout)
        except subprocess.CalledProcessError as e:
            logger.error("Failed to create storage class: %s", e.stderr)
            raise

    def _find_local_chart(self, name: str) -> Path | None:
        """Locate a chart tarball in the charts directory by name.

        Args:
            name: Chart base name, e.g. ``"manila-csi"`` or ``"nfs-csi"``.

        Returns:
            Path to the matching ``.tgz`` tarball, or ``None`` if not found.
        """
        tarballs = sorted(self.charts_dir.glob(f"*{name}*.tgz"))
        if tarballs:
            if len(tarballs) > 1:
                logger.warning(
                    "Multiple tarballs found for chart '%s': %s — using %s",
                    name,
                    tarballs,
                    tarballs[-1],
                )
            return tarballs[-1]

        return None

    def _helm_template_apply(
        self,
        release_name: str,
        chart_path: str,
        namespace: str,
        values_file: Path | None = None,
    ) -> None:
        """Render a Helm chart to YAML and apply it with kubectl.

        Args:
            release_name: Helm release name (used as the template release name)
            chart_path: Path to the chart tarball or directory
            namespace: Kubernetes namespace to render and apply into
            values_file: Optional path to a values YAML file
        """
        cmd = [
            "helm",
            "template",
            release_name,
            chart_path,
            "--namespace",
            namespace,
            "--include-crds",
        ]
        if values_file:
            cmd.extend(["--values", str(values_file)])

        template_result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if template_result.returncode != 0:
            raise RuntimeError(
                f"helm template failed for '{release_name}': {template_result.stderr}"
            )

        apply_result = subprocess.run(
            ["sudo", "k8s", "kubectl", "apply", "--namespace", namespace, "-f", "-"],
            input=template_result.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        if apply_result.returncode != 0:
            raise RuntimeError(f"kubectl apply failed for '{release_name}': {apply_result.stderr}")
        logger.debug("Applied manifests for '%s': %s", release_name, apply_result.stdout)

    def _helm_uninstall(self, release_name: str, namespace: str) -> None:
        """Remove resources for a release by rendering the chart and deleting.

        Args:
            release_name: Helm release name
            namespace: Kubernetes namespace
        """
        logger.info("Removing release '%s' from namespace '%s'", release_name, namespace)

        is_manila = "manila" in release_name
        chart_lookup = "openstack-manila-csi" if is_manila else "csi-driver-nfs"
        values_name = "manila-csi" if is_manila else "nfs-csi"

        chart_path = self._find_local_chart(chart_lookup)
        if chart_path is None:
            logger.warning(
                "Chart not found for release '%s', skipping removal",
                release_name,
            )
            return

        values_file = self.manifests_dir / f"{values_name}-values.yaml"

        cmd = [
            "helm",
            "template",
            release_name,
            str(chart_path),
            "--namespace",
            namespace,
            "--include-crds",
        ]
        if values_file.exists():
            cmd.extend(["--values", str(values_file)])

        template_result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if template_result.returncode != 0:
            logger.warning(
                "helm template failed during removal of '%s': %s",
                release_name,
                template_result.stderr,
            )
            return

        subprocess.run(
            [
                "sudo",
                "k8s",
                "kubectl",
                "delete",
                "--namespace",
                namespace,
                "--ignore-not-found",
                "-f",
                "-",
            ],
            input=template_result.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        logger.info("Release '%s' removed", release_name)

    def _wait_for_deployment(
        self, namespace: str, label_selector: str, timeout: int = 300
    ) -> None:
        """Wait for deployment to be ready.

        Args:
            namespace: Kubernetes namespace
            label_selector: Full label selector string (e.g. 'release=foo' or
                'app.kubernetes.io/instance=foo')
            timeout: Timeout in seconds
        """

        logger.info("Waiting for deployment with label '%s' to be ready", label_selector)
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    [
                        "sudo",
                        "k8s",
                        "kubectl",
                        "get",
                        "pods",
                        "-n",
                        namespace,
                        "-l",
                        label_selector,
                        "-o",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                pods_data = yaml.safe_load(result.stdout)
                if pods_data.get("items"):
                    all_running = all(
                        pod.get("status", {}).get("phase") == "Running"
                        for pod in pods_data["items"]
                    )
                    if all_running:
                        logger.info("Deployment is ready")
                        return
            except (subprocess.CalledProcessError, yaml.YAMLError) as e:
                logger.debug("Waiting for deployment: %s", e)

            time.sleep(10)

        logger.warning("Deployment did not become ready within %s seconds", timeout)

    def _create_volume_snapshot_class(self) -> None:
        """Create a VolumeSnapshotClass for Manila CSI."""
        vsc_manifest = self.manifests_dir / "volume-snapshot-class.yaml"
        if not vsc_manifest.exists():
            logger.warning(
                "VolumeSnapshotClass manifest not found at %s, skipping creation",
                vsc_manifest,
            )
            return

        try:
            result = subprocess.run(
                ["sudo", "k8s", "kubectl", "apply", "-f", str(vsc_manifest)],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info("VolumeSnapshotClass created/updated")
            logger.debug("VolumeSnapshotClass creation: %s", result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to create VolumeSnapshotClass: {e.stderr}") from e

    def _delete_volume_snapshot_class(self) -> None:
        """Delete the VolumeSnapshotClass created during configure."""
        logger.info("Deleting VolumeSnapshotClass 'manila-csi-snapshot-class'")
        subprocess.run(
            [
                "sudo",
                "k8s",
                "kubectl",
                "delete",
                "volumesnapshotclass",
                "manila-csi-snapshot-class",
                "--ignore-not-found",
            ],
            capture_output=True,
            check=False,
        )
