# Manila CSI Charm

Charmhub package name: manila-csi
More information: https://charmhub.io/manila-csi

A principal Juju charm for deploying Manila CSI and NFS CSI drivers on Kubernetes clusters
running on Canonical OpenStack.

## Overview

This charm enables Kubernetes clusters to use OpenStack Manila file shares as persistent storage
for workloads. It deploys:

- **NFS CSI Driver** (optional): Provides NFS protocol support for Manila shares
  (bundled chart: `csi-driver-nfs-4.13.2`).
- **Manila CSI Driver**: Integrates Kubernetes with OpenStack Manila for file share provisioning
  (bundled chart: `openstack-manila-csi-2.35.0`).
- **Storage Class**: A pre-configured `StorageClass` for Manila-backed persistent volumes.
- **OpenStack Secret**: Automatically reads the `cloud-controller-config` Kubernetes secret
  created by the OpenStack cloud-controller-manager and creates the `openstack-manila-secret`
  required by the Manila CSI driver.

## Prerequisites

Before deploying the charm, the following must be in place:

- OpenStack with Octavia for Kubernetes load-balancers
- OpenStack Manila (with Ganesha + CephFS for NFS-backed shares if required)
- Canonical K8s deployed on top of OpenStack
- The OpenStack cloud-controller-manager deployed and the `cloud-controller-config` secret
  present in the `kube-system` namespace

## Deployment

This is a principal charm. Deploy it co-located with a Kubernetes control plane unit:

```bash
juju deploy manila-csi --to $MACHINE_ID
```

On `config-changed`, the charm will:

1. Wait for the Kubernetes cluster to be ready.
2. Create (or ensure) the target namespace.
3. Read the `cloud-controller-config` secret and create `openstack-manila-secret` for the
   Manila CSI driver.
4. Deploy the NFS CSI Helm chart (if `deploy-nfs-csi=true`).
5. Deploy the Manila CSI Helm chart.
6. Create the `StorageClass`.

## Configuration

Some options are **immutable** — they must be set before or at deploy time and cannot be changed
afterwards. Changing them does not remove resources created under the old value; those will persist
until the application is removed.

| Option | Default | Mutable | Description |
|---|---|---|---|
| `manila-share-protocol` | `cephfsnfs` | ✅ | Manila share protocol selector for the storage class (`CEPHFS`, `NFS`, or `cephfsnfs`). |
| `cloud-controller-config-secret` | `cloud-controller-config` | ✅ | Name of the source Kubernetes secret containing OpenStack credentials in `cloud.conf` format. |
| `cloud-controller-config-namespace` | `kube-system` | ✅ | Namespace where the source secret lives. |
| `storage-class-name` | `manila-nfs` | ❌ | Name of the Kubernetes `StorageClass` to create. Renaming creates a new `StorageClass`; the old one is not deleted. |
| `deploy-nfs-csi` | `true` | ❌ | Deploy the NFS CSI driver alongside Manila CSI. Switching from `true` to `false` after deploy does **not** uninstall the NFS CSI driver. |
| `manila-csi-release-name` | `manila-csi` | ❌ | Helm release name for the Manila CSI driver. Changing it leaves resources from the previous release name in place. |
| `nfs-csi-release-name` | `nfs-csi` | ❌ | Helm release name for the NFS CSI driver. Changing it leaves resources from the previous release name in place. |
| `manila-csi-namespace` | `kube-system` | ❌ | Kubernetes namespace for Manila CSI components. Changing it leaves all resources in the old namespace in place. |

```bash
# Use NFS protocol and a custom storage class name
juju config manila-csi manila-share-protocol=NFS storage-class-name=manila-nfs

# Use CEPHFS protocol
juju config manila-csi manila-share-protocol=CEPHFS storage-class-name=manila-cephfs
```

### NFS CSI Driver Deployment

The charm deploys the NFS CSI driver by default (`deploy-nfs-csi=true`). **Important
considerations**:

- **NFS CSI runs in one-to-many mode**: Only one NFS CSI controller should exist per cluster.
- If NFS CSI is already deployed by another application or charm, set `deploy-nfs-csi=false`
  **before deploying** to avoid conflicts. Changing this option after deploy has no effect on
  an already-running NFS CSI driver.

```bash
# Disable NFS CSI deployment when it is already present in the cluster
juju config manila-csi deploy-nfs-csi=false
```

## Helm Chart Resources

The charm ships with bundled Helm charts (`charts/` directory). You can override them by
attaching Juju resources:

```bash
# Attach a custom Manila CSI chart tarball
juju attach-resource manila-csi manila-csi-chart=./openstack-manila-csi-<version>.tgz

# Attach a custom NFS CSI chart tarball
juju attach-resource manila-csi nfs-csi-chart=./csi-driver-nfs-<version>.tgz
```

If no resource is attached, the charm falls back to the chart tarballs in the `charts/`
directory:

- `charts/openstack-manila-csi-2.35.0.tgz`
- `charts/csi-driver-nfs-4.13.2.tgz`

To update the bundled charts before building:

```bash
helm repo add cpo-helm-charts https://kubernetes.github.io/cloud-provider-openstack
helm pull cpo-helm-charts/openstack-manila-csi --destination charts/

helm repo add csi-driver-nfs https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts
helm pull csi-driver-nfs/csi-driver-nfs --destination charts/
```

## Other resources

- [Contributing](CONTRIBUTING.md)
- [OpenStack Manila CSI Documentation](https://github.com/kubernetes/cloud-provider-openstack/blob/master/docs/manila-csi-plugin/using-manila-csi-plugin.md)
- [Juju charm documentation](https://documentation.ubuntu.com/juju/3.6/howto/manage-charms/)
