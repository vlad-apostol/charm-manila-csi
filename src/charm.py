#!/usr/bin/env python3
# Copyright 2026 vlad.apostol@canonical.com
# See LICENSE file for licensing details.

"""Manila CSI subordinate charm for Kubernetes
integration with OpenStack Manila.
"""

import logging
from pathlib import Path

import ops

from manila_csi import ManilaCsiManager

logger = logging.getLogger(__name__)


class ManilaCsiCharm(ops.CharmBase):
    """Manila CSI subordinate charm.

    This charm deploys the Manila CSI and optionally NFS CSI drivers to enable
    Kubernetes clusters to use OpenStack Manila
    file shares as persistent storage.
    """

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self.manager = ManilaCsiManager(
            charm_dir=Path(self.charm_dir),
            app_name=self.app.name,
            model_name=self.model.name,
        )

        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.update_status, self._on_update_status)
        framework.observe(self.on.remove, self._on_remove)
        framework.observe(self.on.snapshot_create_action, self._on_snapshot_create)
        framework.observe(self.on.snapshot_list_action, self._on_snapshot_list)
        framework.observe(self.on.snapshot_delete_action, self._on_snapshot_delete)
        framework.observe(self.on.snapshot_delete_all_action, self._on_snapshot_delete_all)
        framework.observe(self.on.snapshot_restore_action, self._on_snapshot_restore)

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle install event."""
        self.unit.status = ops.MaintenanceStatus("Installing Manila CSI components")
        try:
            self.manager.install()
            self.unit.status = ops.ActiveStatus("Manila CSI installed")
        except RuntimeError as e:
            logger.error("Installation failed: %s", e)
            self.unit.status = ops.BlockedStatus(f"Installation failed: {e}")

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle config-changed event."""
        self.unit.status = ops.MaintenanceStatus("Configuring Manila CSI")
        try:
            # Get resource paths if available
            manila_chart_path = None
            nfs_chart_path = None

            try:
                manila_chart_path = self.model.resources.fetch("manila-csi-chart")  # noqa: E501
            except (ops.ModelError, NameError):
                logger.debug("Manila CSI chart resource not provided")

            try:
                nfs_chart_path = self.model.resources.fetch("nfs-csi-chart")
            except (ops.ModelError, NameError):
                logger.debug("NFS CSI chart resource not provided")

            config = {
                "manila_share_protocol": self.config["manila-share-protocol"],
                "storage_class_name": self.config["storage-class-name"],
                "manila_csi_release": self.config["manila-csi-release-name"],
                "nfs_csi_release": self.config["nfs-csi-release-name"],
                "deploy_nfs_csi": self.config["deploy-nfs-csi"],
                "namespace": self.config["manila-csi-namespace"],
                "manila_csi_chart_path": manila_chart_path,
                "nfs_csi_chart_path": nfs_chart_path,
                "cloud_controller_config_secret": self.config["cloud-controller-config-secret"],
                "cloud_controller_config_namespace": self.config[
                    "cloud-controller-config-namespace"
                ],
            }

            self.manager.configure(config)
            self.unit.status = ops.ActiveStatus("Manila CSI configured")
        except Exception as e:
            logger.error("Configuration failed: %s", e)
            self.unit.status = ops.BlockedStatus(f"Configuration failed: {e}")

    def _on_update_status(self, event: ops.UpdateStatusEvent):
        """Handle update-status event."""
        try:
            if self.manager.is_ready(str(self.config["manila-csi-namespace"])):
                nfs_status = (
                    "with NFS CSI" if self.config["deploy-nfs-csi"] else "NFS CSI: external"
                )
                self.unit.status = ops.ActiveStatus(f"Manila CSI ready ({nfs_status})")
            else:
                self.unit.status = ops.WaitingStatus("Manila CSI not ready")
        except RuntimeError as e:
            logger.warning("Status check failed: %s", e)
            self.unit.status = ops.UnknownStatus()

    def _on_remove(self, event: ops.RemoveEvent):
        """Handle remove event."""
        self.unit.status = ops.MaintenanceStatus("Removing Manila CSI components")
        try:
            config = {
                "manila_csi_release": self.config["manila-csi-release-name"],
                "nfs_csi_release": self.config["nfs-csi-release-name"],
                "deploy_nfs_csi": self.config["deploy-nfs-csi"],
                "namespace": self.config["manila-csi-namespace"],
                "storage_class_name": self.config["storage-class-name"],
            }
            self.manager.remove(config)
        except Exception as e:
            logger.error("Removal failed: %s", e)

    def _on_snapshot_create(self, event: ops.ActionEvent) -> None:
        """Handle snapshot-create action."""
        pvc_name = event.params.get("pvc-name") or None
        namespace = event.params.get("namespace") or None
        try:
            created = self.manager.snapshot_create(pvc_name=pvc_name, namespace=namespace)
            event.set_results(
                {
                    "created": len(created),
                    "snapshots": "\n".join(
                        f"snapshot: {s['snapshot_name']} | namespace: {s['namespace']}"
                        for s in created
                    ),
                }
            )
        except RuntimeError as e:
            event.fail(str(e))

    def _on_snapshot_list(self, event: ops.ActionEvent) -> None:
        """Handle snapshot-list action."""
        snapshot_name = event.params.get("snapshot-name") or None
        pvc_name = event.params.get("pvc-name") or None
        namespace = event.params.get("namespace") or None
        try:
            snapshots = self.manager.snapshot_list(
                snapshot_name=snapshot_name, pvc_name=pvc_name, namespace=namespace
            )
            if not snapshots:
                event.set_results({"snapshots": "No Manila CSI snapshots found."})
                return
            event.set_results(
                {
                    "count": len(snapshots),
                    "snapshots": "\n".join(
                        f"snapshot: {s['snapshot_name']} | namespace: {s['namespace']}"
                        for s in snapshots
                    ),
                }
            )
        except RuntimeError as e:
            event.fail(str(e))

    def _on_snapshot_delete(self, event: ops.ActionEvent) -> None:
        """Handle snapshot-delete action (single snapshot)."""
        snapshot_name = event.params["snapshot-name"]
        namespace = event.params["namespace"]
        try:
            deleted = self.manager.snapshot_delete(
                snapshot_name=snapshot_name,
                namespace=namespace,
            )
            s = deleted[0]
            event.set_results(
                {
                    "deleted": f"snapshot: {s['snapshot_name']} | namespace: {s['namespace']}",
                }
            )
        except RuntimeError as e:
            event.fail(str(e))

    def _on_snapshot_delete_all(self, event: ops.ActionEvent) -> None:
        """Handle snapshot-delete-all action (bulk delete with confirmation)."""
        i_really_mean_it = bool(event.params.get("i-really-mean-it", False))
        try:
            deleted = self.manager.snapshot_delete(i_really_mean_it=i_really_mean_it)
            event.set_results(
                {
                    "deleted": len(deleted),
                    "snapshots": "\n".join(
                        f"snapshot: {s['snapshot_name']} | namespace: {s['namespace']}"
                        for s in deleted
                    ),
                }
            )
        except RuntimeError as e:
            event.fail(str(e))

    def _on_snapshot_restore(self, event: ops.ActionEvent) -> None:
        """Handle snapshot-restore action."""
        snapshot_name = event.params["snapshot-name"]
        namespace = event.params["namespace"]
        size = event.params.get("size") or None
        try:
            result = self.manager.snapshot_restore(
                snapshot_name=snapshot_name,
                namespace=namespace,
                size=size,
            )
            event.set_results(
                {
                    "restored": (f"pvc: {result['pvc_name']} | namespace: {result['namespace']}"),
                }
            )
        except RuntimeError as e:
            event.fail(str(e))


if __name__ == "__main__":  # pragma: nocover
    ops.main(ManilaCsiCharm)
