# GCP OpenShift MCP Server

An intelligent Model Context Protocol (MCP) server for managing OpenShift clusters on Google Cloud Platform (GCP).

## Features

- **Create OpenShift Clusters**: Deploy OpenShift clusters on GCP with custom names and configurations
- **Delete Clusters**: Clean up clusters and all associated GCP resources
- **Cluster Management**: Get status, list clusters, and retrieve kubeconfigs
- **Submariner Cloud Preparation**: Prepare cloud infrastructure (firewall rules, gateway nodes) for Submariner on GCP, AWS, Azure, and OpenStack
- **Kubeconfig Management**: Rename contexts/users and merge multiple kubeconfigs to avoid conflicts
- **Submariner Integration**: Works seamlessly with the Submariner MCP server for multi-cluster networking
- **Reusable**: Accept custom cluster names as parameters for flexible deployment

## Prerequisites

Before using this MCP server, ensure you have the following tools installed:

1. **openshift-install** - OpenShift installer CLI
   - Download from: https://console.redhat.com/openshift/downloads
   - Verify: `openshift-install version`

2. **gcloud** - Google Cloud SDK
   - Install: https://cloud.google.com/sdk/docs/install
   - Configure: `gcloud auth login` and `gcloud config set project YOUR_PROJECT_ID`
   - Verify: `gcloud version`

3. **kubectl** - Kubernetes CLI
   - Install: https://kubernetes.io/docs/tasks/tools/
   - Verify: `kubectl version --client`

4. **subctl** - Submariner CLI (for cloud_prepare operations)
   - Install: https://submariner.io/operations/deployment/subctl/
   - Verify: `subctl version`

5. **Python 3.10+**
   - Verify: `python3 --version`

6. **SSH Key Pair**
   - Generate: `ssh-keygen -t rsa -b 4096`

## Installation

1. Clone or navigate to the repository:
   ```bash
   cd gcp-openshift-mcp-server
   ```

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Ensure GCP credentials are configured:
   ```bash
   gcloud auth application-default login
   ```

4. **Setup Pull-Secret** (Required - one time setup):
   ```bash
   # Download your pull-secret from https://console.redhat.com/openshift/install/gcp/installer-provisioned
   # Then copy it to the required location:
   mkdir -p ~/.config/openshift
   cp ~/Downloads/pull-secret.json ~/.config/openshift/pull-secret.json
   chmod 600 ~/.config/openshift/pull-secret.json
   ```

   The MCP server will automatically read the pull-secret from this location. You never need to pass it as a parameter.

## Usage

### Running the Server

Start the MCP server:

```bash
python server.py
```

The server will run as a stdio-based MCP server and can be integrated with MCP clients.

### Available Tools

#### 1. check_prerequisites

Check if all required tools are installed.

**Example Request:**
```json
{
  "name": "check_prerequisites",
  "arguments": {}
}
```

**Example Response:**
```json
{
  "success": true,
  "checks": {
    "openshift_install": {
      "installed": true,
      "version": "openshift-install 4.14.0"
    },
    "gcloud": {
      "installed": true,
      "version": "Google Cloud SDK 450.0.0"
    },
    "kubectl": {
      "installed": true
    }
  },
  "message": "All prerequisites met"
}
```

#### 2. create_cluster

Create a new OpenShift cluster on GCP with a custom name. The pull-secret is automatically read from `~/.config/openshift/pull-secret.json`.

**Parameters:**
- `cluster_name` (required): Unique name for the cluster (e.g., "submariner-gcp-1")
- `ssh_public_key` (required): SSH public key for cluster access
- `region` (optional): GCP region, default: "us-east1"
- `base_domain` (optional): Base domain, default: "devcluster.openshift.com"
- `worker_replicas` (optional): Number of worker nodes, default: 3
- `master_replicas` (optional): Number of master nodes, default: 3
- `network_type` (optional): Network plugin, default: "OVNKubernetes"

**Example Request:**
```json
{
  "name": "create_cluster",
  "arguments": {
    "cluster_name": "submariner-gcp-1",
    "ssh_public_key": "ssh-rsa AAAAB3...",
    "region": "us-east1",
    "worker_replicas": 3,
    "master_replicas": 3
  }
}
```

**Example Response:**
```json
{
  "success": true,
  "cluster_name": "submariner-gcp-1",
  "cluster_dir": "./clusters/submariner-gcp-1",
  "kubeconfig_path": "./clusters/submariner-gcp-1/auth/kubeconfig",
  "message": "Cluster submariner-gcp-1 created successfully"
}
```

#### 3. delete_cluster

Delete an OpenShift cluster and clean up all GCP resources.

**Parameters:**
- `cluster_name` (required): Name of the cluster to delete

**Example Request:**
```json
{
  "name": "delete_cluster",
  "arguments": {
    "cluster_name": "submariner-gcp-1"
  }
}
```

**Example Response:**
```json
{
  "success": true,
  "cluster_name": "submariner-gcp-1",
  "message": "Cluster submariner-gcp-1 deleted successfully"
}
```

#### 4. get_cluster_status

Get detailed status of a cluster.

**Parameters:**
- `cluster_name` (required): Name of the cluster

**Example Request:**
```json
{
  "name": "get_cluster_status",
  "arguments": {
    "cluster_name": "submariner-gcp-1"
  }
}
```

**Example Response:**
```json
{
  "success": true,
  "cluster_name": "submariner-gcp-1",
  "status": "created",
  "cluster_dir": "./clusters/submariner-gcp-1",
  "kubeconfig_path": "./clusters/submariner-gcp-1/auth/kubeconfig",
  "accessible": true,
  "nodes": "NAME                           STATUS   ROLES    AGE   VERSION\n...",
  "region": "us-east1",
  "base_domain": "devcluster.openshift.com"
}
```

#### 5. list_clusters

List all GCP OpenShift clusters.

**Example Request:**
```json
{
  "name": "list_clusters",
  "arguments": {}
}
```

**Example Response:**
```json
{
  "success": true,
  "clusters": [
    {
      "name": "submariner-gcp-1",
      "status": "created",
      "cluster_dir": "./clusters/submariner-gcp-1",
      "region": "us-east1",
      "kubeconfig_available": true
    },
    {
      "name": "submariner-gcp-2",
      "status": "created",
      "cluster_dir": "./clusters/submariner-gcp-2",
      "region": "us-east1",
      "kubeconfig_available": true
    }
  ],
  "count": 2
}
```

#### 6. get_kubeconfig

Get the kubeconfig file content for a cluster.

**Parameters:**
- `cluster_name` (required): Name of the cluster

**Example Request:**
```json
{
  "name": "get_kubeconfig",
  "arguments": {
    "cluster_name": "submariner-gcp-1"
  }
}
```

**Example Response:**
```json
{
  "success": true,
  "cluster_name": "submariner-gcp-1",
  "kubeconfig_path": "./clusters/submariner-gcp-1/auth/kubeconfig",
  "kubeconfig_content": "apiVersion: v1\nclusters:\n..."
}
```

#### 7. cloud_prepare

Prepare cloud infrastructure for Submariner using `subctl cloud prepare`. **This MUST be run before joining clusters to Submariner.** It configures firewall rules, security groups, gateway nodes, and other cloud-specific requirements.

**Supported cloud providers:** GCP, AWS, Azure, OpenStack

**Parameters:**
- `cluster_name` (required): Name of the cluster to prepare
- `cloud_provider` (optional): Cloud provider type (gcp, aws, azure, openstack), default: "gcp"

**Example Request:**
```json
{
  "name": "cloud_prepare",
  "arguments": {
    "cluster_name": "submariner-gcp-1",
    "cloud_provider": "gcp"
  }
}
```

**Example Response:**
```json
{
  "success": true,
  "cluster_name": "submariner-gcp-1",
  "cloud_provider": "gcp",
  "message": "Cloud infrastructure prepared successfully for submariner-gcp-1"
}
```

**What it does:**
- Creates dedicated gateway machinesets
- Deploys gateway nodes with submariner-gw label
- Configures GCP firewall rules for IPsec (UDP 500, 4500, ESP protocol)
- Prepares infrastructure required for Submariner tunnels

#### 8. rename_kubeconfig_context

Rename context and user in a cluster's kubeconfig to avoid conflicts when merging multiple kubeconfigs. This is essential when merging kubeconfigs that have the same context/user names (like "admin").

**Parameters:**
- `cluster_name` (required): Name of the cluster whose kubeconfig to rename
- `new_context_name` (optional): New context name (defaults to "{cluster_name}-admin")
- `new_user_name` (optional): New user name (defaults to "{cluster_name}-admin")

**Example Request:**
```json
{
  "name": "rename_kubeconfig_context",
  "arguments": {
    "cluster_name": "submariner-gcp-1"
  }
}
```

**Example Response:**
```json
{
  "success": true,
  "cluster_name": "submariner-gcp-1",
  "old_context_name": "admin",
  "new_context_name": "submariner-gcp-1-admin",
  "old_user_name": "admin",
  "new_user_name": "submariner-gcp-1-admin",
  "message": "Successfully renamed context and user in kubeconfig for submariner-gcp-1"
}
```

**Use case:**
When you have multiple clusters with kubeconfigs that all have "admin" as the context and user name, you need to rename them before merging to avoid conflicts.

#### 9. merge_kubeconfigs

Merge kubeconfigs from multiple clusters into a single file. This allows you to manage multiple clusters from a single kubeconfig file and switch between them using `kubectl config use-context`.

**Important:** Before merging, ensure contexts and users have unique names using `rename_kubeconfig_context`.

**Parameters:**
- `cluster_names` (required): List of cluster names to merge
- `output_path` (optional): Path where the merged kubeconfig should be saved (default: "/tmp/merged-kubeconfig.yaml")

**Example Request:**
```json
{
  "name": "merge_kubeconfigs",
  "arguments": {
    "cluster_names": ["submariner-gcp-1", "submariner-gcp-2"],
    "output_path": "/tmp/merged-kubeconfig.yaml"
  }
}
```

**Example Response:**
```json
{
  "success": true,
  "cluster_names": ["submariner-gcp-1", "submariner-gcp-2"],
  "output_path": "/tmp/merged-kubeconfig.yaml",
  "message": "Successfully merged 2 kubeconfigs to /tmp/merged-kubeconfig.yaml"
}
```

**Usage with kubectl:**
```bash
export KUBECONFIG=/tmp/merged-kubeconfig.yaml
kubectl config get-contexts
kubectl config use-context submariner-gcp-2-admin
kubectl get nodes
```

## Integration with Submariner MCP Server

This server is designed to work seamlessly with the Submariner MCP server for multi-cluster networking.

### Workflow Example

1. **Create two clusters:**
   ```json
   // Create cluster 1
   {
     "name": "create_cluster",
     "arguments": {
       "cluster_name": "submariner-gcp-1",
       "ssh_public_key": "ssh-rsa AAAAB3..."
     }
   }

   // Create cluster 2
   {
     "name": "create_cluster",
     "arguments": {
       "cluster_name": "submariner-gcp-2",
       "ssh_public_key": "ssh-rsa AAAAB3..."
     }
   }
   ```

2. **Get kubeconfigs:**
   ```json
   // Get kubeconfig for cluster 1
   {
     "name": "get_kubeconfig",
     "arguments": {
       "cluster_name": "submariner-gcp-1"
     }
   }

   // Get kubeconfig for cluster 2
   {
     "name": "get_kubeconfig",
     "arguments": {
       "cluster_name": "submariner-gcp-2"
     }
   }
   ```

3. **Use Submariner MCP server to install Submariner:**
   - Use the kubeconfig contents with the Submariner MCP server's `add_cluster` tool
   - Follow the Submariner workflow: analyze CIDRs, configure globalnet, deploy broker, join clusters

## Architecture

### Directory Structure

```
gcp-openshift-mcp-server/
├── server.py           # Main MCP server
├── requirements.txt    # Python dependencies
├── README.md          # This file
└── clusters/          # Clusters directory
    ├── submariner-gcp-1/      # Cluster 1 directory
    │   ├── install-config.yaml
    │   ├── metadata.json
    │   ├── auth/
    │   │   └── kubeconfig
    │   └── .openshift_install.log
    └── submariner-gcp-2/      # Cluster 2 directory
        ├── install-config.yaml
        ├── metadata.json
        ├── auth/
        │   └── kubeconfig
        └── .openshift_install.log
```

### Cluster Lifecycle

1. **Creating**: Cluster is being provisioned by openshift-install
2. **Created**: Cluster is ready and kubeconfig is available
3. **Failed**: Cluster creation failed
4. **Deleting**: Cluster is being destroyed

## Error Handling

The server provides detailed error messages for common issues:

- Missing prerequisites (tools not installed)
- Duplicate cluster names
- Cluster not found
- Kubeconfig not available
- GCP authentication issues
- Cluster creation/deletion failures

## Logging

The server logs all operations to help with debugging:

```python
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

## Troubleshooting

### Cluster Creation Fails

1. Check GCP credentials: `gcloud auth list`
2. Verify project ID: `gcloud config get-value project`
3. Check quota limits in GCP console
4. Review `.openshift_install.log` in the cluster directory

### Kubeconfig Not Found

- Cluster creation may still be in progress
- Check cluster status with `get_cluster_status`
- Review installation logs

### Prerequisites Check Fails

- Ensure all required tools are in PATH
- Verify installations: `openshift-install version`, `gcloud version`, `kubectl version`

## Contributing

To extend this server:

1. Add new tools in the `@app.list_tools()` decorator
2. Implement handlers in the `@app.call_tool()` function
3. Add methods to the `GCPOpenShiftManager` class

## License

This is part of the OpenShift development toolkit.

## Support

For issues related to:
- OpenShift installation: https://docs.openshift.com/
- GCP: https://cloud.google.com/docs
- MCP protocol: https://github.com/anthropics/mcp

## Version

Current version: 1.1.0

## Changelog

### 1.1.0 (2025-10-29)
- Added `cloud_prepare` tool for Submariner infrastructure preparation (GCP, AWS, Azure, OpenStack)
- Added `rename_kubeconfig_context` tool to rename contexts and users to avoid conflicts
- Added `merge_kubeconfigs` tool to merge multiple kubeconfigs into a single file
- Enhanced README with detailed documentation for all new tools
- Improved multi-cluster management capabilities

### 1.0.0 (2025-10-29)
- Initial release
- Support for creating and deleting GCP OpenShift clusters
- Integration with Submariner MCP server
- Cluster status and kubeconfig retrieval
