# Copyright 2026 vlad.apostol@canonical.com
# See LICENSE file for licensing details.
#
# To learn more about testing, see https://documentation.ubuntu.com/ops/latest/explanation/testing/

import pytest
from ops import testing

from charm import ManilaCsiCharm


def test_install(monkeypatch: pytest.MonkeyPatch):
    """Test that the charm handles the install event correctly."""
    # Arrange:
    ctx = testing.Context(ManilaCsiCharm)
    state_in = testing.State()

    # Mock the manager install method
    install_called = []

    def mock_install(self):
        install_called.append(True)

    monkeypatch.setattr("manila_csi.ManilaCsiManager.install", mock_install)

    # Act:
    state_out = ctx.run(ctx.on.install(), state_in)

    # Assert:
    assert len(install_called) == 1
    assert state_out.unit_status == testing.ActiveStatus("Manila CSI installed")


def test_config_changed(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Test that the charm handles config-changed event correctly."""
    # Arrange:
    ctx = testing.Context(ManilaCsiCharm)
    # Provide placeholder resource files so model.resources.fetch() doesn't raise
    manila_chart = tmp_path / "manila-csi-chart.tgz"
    nfs_chart = tmp_path / "nfs-csi-chart.tgz"
    manila_chart.touch()
    nfs_chart.touch()
    state_in = testing.State(
        resources={
            testing.Resource(name="manila-csi-chart", path=manila_chart),
            testing.Resource(name="nfs-csi-chart", path=nfs_chart),
        }
    )

    # Mock the manager configure method
    configure_called = []

    def mock_configure(self, config):
        configure_called.append(config)

    def mock_install(self):
        pass

    monkeypatch.setattr("manila_csi.ManilaCsiManager.configure", mock_configure)
    monkeypatch.setattr("manila_csi.ManilaCsiManager.install", mock_install)

    # Act:
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Assert:
    assert len(configure_called) == 1
    assert state_out.unit_status == testing.ActiveStatus("Manila CSI configured")

    # Check that configuration was passed correctly (values match charmcraft.yaml defaults)
    config = configure_called[0]
    assert config["manila_share_protocol"] == "cephfsnfstype"
    assert config["storage_class_name"] == "manila-nfs"
    assert config["namespace"] == "kube-system"


def test_update_status_ready(monkeypatch: pytest.MonkeyPatch):
    """Test that the charm reports ready status when CSI is ready."""
    # Arrange:
    ctx = testing.Context(ManilaCsiCharm)
    state_in = testing.State()

    # Mock the manager methods
    def mock_is_ready(self, namespace):
        return True

    def mock_install(self):
        pass

    monkeypatch.setattr("manila_csi.ManilaCsiManager.is_ready", mock_is_ready)
    monkeypatch.setattr("manila_csi.ManilaCsiManager.install", mock_install)

    # Act:
    state_out = ctx.run(ctx.on.update_status(), state_in)

    # Assert: deploy-nfs-csi defaults to true, so status includes "with NFS CSI"
    assert state_out.unit_status == testing.ActiveStatus("Manila CSI ready (with NFS CSI)")


def test_remove(monkeypatch: pytest.MonkeyPatch):
    """Test that the charm passes the correct config to manager.remove on remove event."""
    # Arrange:
    ctx = testing.Context(ManilaCsiCharm)
    state_in = testing.State()

    remove_called = []

    def mock_remove(self, config):
        remove_called.append(config)

    def mock_install(self):
        pass

    monkeypatch.setattr("manila_csi.ManilaCsiManager.remove", mock_remove)
    monkeypatch.setattr("manila_csi.ManilaCsiManager.install", mock_install)

    # Act:
    ctx.run(ctx.on.remove(), state_in)

    # Assert:
    assert len(remove_called) == 1
    config = remove_called[0]
    assert "storage_class_name" in config
    assert config["storage_class_name"] == "manila-nfs"
    assert "namespace" in config
    assert config["namespace"] == "kube-system"


def test_update_status_not_ready(monkeypatch: pytest.MonkeyPatch):
    """Test that the charm reports waiting status when CSI is not ready."""
    # Arrange:
    ctx = testing.Context(ManilaCsiCharm)
    state_in = testing.State()

    # Mock the manager methods
    def mock_is_ready(self, namespace):
        return False

    def mock_install(self):
        pass

    monkeypatch.setattr("manila_csi.ManilaCsiManager.is_ready", mock_is_ready)
    monkeypatch.setattr("manila_csi.ManilaCsiManager.install", mock_install)

    # Act:
    state_out = ctx.run(ctx.on.update_status(), state_in)

    # Assert:
    assert state_out.unit_status == testing.WaitingStatus("Manila CSI not ready")
