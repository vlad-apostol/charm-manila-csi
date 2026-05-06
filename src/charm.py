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
            if self.manager.is_ready(self.config["manila-csi-namespace"]):
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
            }
            self.manager.remove(config)
        except Exception as e:
            logger.error("Removal failed: %s", e)


if __name__ == "__main__":  # pragma: nocover
    ops.main(ManilaCsiCharm)
