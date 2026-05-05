<!-- Todo -->
# TO BE REVISED
<!--
Avoid using this README file for information that is maintained or published elsewhere, e.g.:

* charmcraft.yaml > published on Charmhub
* documentation > published on (or linked to from) Charmhub
* detailed contribution guide > documentation or CONTRIBUTING.md

Use links instead.
-->

# Manila CSI Charm

Charmhub package name: manila-csi
More information: https://charmhub.io/manila-csi

A principal Juju charm for deploying Manila CSI and NFS CSI drivers on Kubernetes clusters running on Canonical OpenStack.

## Overview

This charm enables Kubernetes clusters to use OpenStack Manila file shares as persistent storage for workloads. It deploys:

- **Manila CSI Driver**: Integrates Kubernetes with OpenStack Manila for file share provisioning
- **NFS CSI Driver** (optional): Provides NFS protocol support for Manila shares
- **Storage Classes**: Pre-configured storage classes for Manila-backed persistent volumes

## Features

- Subordinate charm that attaches to Kubernetes worker units
- Local Helm chart storage (no external repository dependencies required)
- Pre-defined manifests for easy customization
- Configurable Manila share protocols (CEPHFS, NFS)
- Optional NFS CSI driver deployment
- Automatic storage class creation

## Prerequisites

### OpenStack Manila Secret

Before deploying this charm, create a Kubernetes secret containing OpenStack credentials with Manila service access:

```bash
kubectl create secret generic cloud-config \
  --from-file=clouds.yaml=/path/to/clouds.yaml \
  --namespace=kube-system
```

## Deployment

Deploy the charm as a subordinate to Kubernetes worker units:

```bash
juju deploy manila-csi
juju integrate manila-csi:juju-info kubernetes-worker:juju-info
```

For K8s charm:

```bash
juju deploy manila-csi
juju integrate manila-csi:juju-info k8s:juju-info
```

## Configuration

Configure the charm according to your Manila deployment:

```bash
# Set Manila share protocol (CEPHFS or NFS)
juju config manila-csi manila-share-protocol=CEPHFS

# Set custom storage class name
juju config manila-csi storage-class-name=manila-cephfs
```

### NFS CSI Driver Deployment

The charm can optionally deploy the NFS CSI driver. **Important considerations**:

- **NFS CSI runs in one-to-many mode**: Only one NFS CSI controller should exist per cluster
- **Default behavior**: `deploy-nfs-csi=false` (does not deploy NFS CSI)

**When to enable NFS CSI deployment** (`deploy-nfs-csi=true`):

```bash
juju config manila-csi deploy-nfs-csi=true
```

- You need Manila shares with NFS protocol
- NFS CSI driver is **not** already deployed in your cluster

**When to keep NFS CSI disabled** (default):

- NFS CSI is already deployed by another application/charm
- You're only using CEPHFS protocol
- You plan to deploy NFS CSI separately

If NFS CSI is already running in your cluster and you enable `deploy-nfs-csi=true`, you may encounter conflicts with multiple NFS CSI controllers.

## Local Helm Charts

To use local Helm charts, place them in the `charts/` directory before building:

```bash
# Download Manila CSI chart
helm repo add cpo-helm-charts https://kubernetes.github.io/cloud-provider-openstack
helm pull cpo-helm-charts/openstack-manila-csi --untar --untardir charts/
mv charts/openstack-manila-csi charts/manila-csi
```

See `charts/README.md` and `manifests/README.md` for more details.

## Other resources

- [Contributing](CONTRIBUTING.md)
- [OpenStack Manila CSI Documentation](https://github.com/kubernetes/cloud-provider-openstack/blob/master/docs/manila-csi-plugin/using-manila-csi-plugin.md)
- See the [Juju documentation](https://documentation.ubuntu.com/juju/3.6/howto/manage-charms/) for more information about developing and improving charms.
