#!/usr/bin/env python3
"""
GCP OpenShift MCP Server

An intelligent Model Context Protocol server for managing OpenShift clusters on GCP.

This server provides tools for:
- Creating OpenShift clusters on GCP with custom names
- Deleting clusters
- Getting cluster status and kubeconfig
- Preparing cloud infrastructure for Submariner (GCP, AWS, Azure, OpenStack)
- Renaming kubeconfig contexts and users to avoid conflicts
- Merging multiple kubeconfigs into a single file
- Installing Submariner for multi-cluster networking

Requirements:
- openshift-install CLI tool installed
- gcloud CLI tool installed and configured
- kubectl installed
- subctl (for Submariner operations)
- Python 3.10+

Usage:
    python server.py
"""

import asyncio
import json
import logging
import os
import shutil
import yaml
from pathlib import Path
from typing import Any, Optional, Dict, List
from dataclasses import dataclass

# MCP SDK imports
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("gcp-openshift-mcp")


@dataclass
class ClusterConfig:
    """Configuration for a GCP OpenShift cluster"""
    name: str
    region: str = "us-east1"
    base_domain: str = "devcluster.openshift.com"
    worker_replicas: int = 3
    master_replicas: int = 3
    network_type: str = "OVNKubernetes"
    cluster_dir: Optional[str] = None
    kubeconfig_path: Optional[str] = None
    status: str = "not_created"  # not_created, creating, created, failed, deleting


class CommandRunner:
    """Runs shell commands"""

    async def run(
        self,
        cmd: List[str],
        capture_output: bool = True,
        check: bool = False,
        cwd: Optional[str] = None
    ) -> tuple[int, str, str]:
        """Run a command and return (returncode, stdout, stderr)"""
        try:
            logger.info(f"Running: {' '.join(cmd)}")
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE if capture_output else None,
                stderr=asyncio.subprocess.PIPE if capture_output else None,
                cwd=cwd
            )
            stdout, stderr = await result.communicate()

            stdout_str = stdout.decode() if stdout else ""
            stderr_str = stderr.decode() if stderr else ""

            if check and result.returncode != 0:
                raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{stderr_str}")

            return (result.returncode, stdout_str, stderr_str)
        except Exception as e:
            logger.error(f"Command failed: {cmd}, error: {e}")
            return (1, "", str(e))


class GCPOpenShiftManager:
    """Manages GCP OpenShift cluster operations"""

    def __init__(self, base_dir: str = "./clusters"):
        self.base_dir = os.path.abspath(base_dir)
        self.clusters: Dict[str, ClusterConfig] = {}
        self.runner = CommandRunner()
        self.pull_secret_path = os.path.expanduser("~/.config/openshift/pull-secret.json")
        self._load_existing_clusters()

    def _get_pull_secret(self) -> str:
        """Read pull-secret from secure location

        The pull-secret should be stored at ~/.config/openshift/pull-secret.json
        This ensures the secret is never passed as a parameter or logged.

        Returns:
            str: The pull-secret JSON string

        Raises:
            FileNotFoundError: If pull-secret file doesn't exist with helpful instructions
        """
        if not os.path.exists(self.pull_secret_path):
            config_dir = os.path.dirname(self.pull_secret_path)
            raise FileNotFoundError(
                f"\n\n"
                f"❌ Pull-secret not found!\n\n"
                f"Please copy your OpenShift pull-secret to: {self.pull_secret_path}\n\n"
                f"Steps:\n"
                f"1. Download pull-secret from: https://cloud.redhat.com/openshift/install/pull-secret\n"
                f"2. Create directory: mkdir -p {config_dir}\n"
                f"3. Save it to: {self.pull_secret_path}\n"
                f"4. Set permissions: chmod 600 {self.pull_secret_path}\n\n"
                f"The pull-secret will be read securely from this location and never logged.\n"
            )

        try:
            with open(self.pull_secret_path, 'r') as f:
                pull_secret = f.read().strip()

            # Validate it's valid JSON
            json.loads(pull_secret)

            logger.info(f"Pull-secret loaded from {self.pull_secret_path}")
            return pull_secret
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid pull-secret format in {self.pull_secret_path}. "
                f"It must be valid JSON. Error: {e}"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to read pull-secret: {e}")

    def _load_existing_clusters(self):
        """Load existing cluster directories"""
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
            return

        for item in os.listdir(self.base_dir):
            item_path = os.path.join(self.base_dir, item)
            if os.path.isdir(item_path) and not item.startswith('.'):
                # Check if it looks like a cluster directory
                metadata_file = os.path.join(item_path, "metadata.json")
                if os.path.exists(metadata_file):
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                            cluster_name = metadata.get('clusterName', item)

                            kubeconfig_path = os.path.join(item_path, "auth", "kubeconfig")
                            status = "created" if os.path.exists(kubeconfig_path) else "creating"

                            self.clusters[cluster_name] = ClusterConfig(
                                name=cluster_name,
                                cluster_dir=item_path,
                                kubeconfig_path=kubeconfig_path if os.path.exists(kubeconfig_path) else None,
                                status=status
                            )
                            logger.info(f"Loaded existing cluster: {cluster_name}")
                    except Exception as e:
                        logger.warning(f"Failed to load cluster from {item_path}: {e}")

    async def check_prerequisites(self) -> Dict[str, Any]:
        """Check if required tools are installed"""

        checks = {}

        # Check openshift-install
        returncode, stdout, stderr = await self.runner.run(["openshift-install", "version"])
        checks["openshift_install"] = {
            "installed": returncode == 0,
            "version": stdout.strip() if returncode == 0 else None
        }

        # Check gcloud
        returncode, stdout, stderr = await self.runner.run(["gcloud", "version"])
        checks["gcloud"] = {
            "installed": returncode == 0,
            "version": stdout.split('\n')[0] if returncode == 0 else None
        }

        # Check kubectl
        returncode, stdout, stderr = await self.runner.run(["kubectl", "version", "--client"])
        checks["kubectl"] = {
            "installed": returncode == 0
        }

        all_installed = all(check["installed"] for check in checks.values())

        return {
            "success": all_installed,
            "checks": checks,
            "message": "All prerequisites met" if all_installed else "Missing required tools"
        }

    async def create_cluster(
        self,
        cluster_name: str,
        ssh_public_key: str,
        region: str = "us-east1",
        base_domain: str = "devcluster.openshift.com",
        worker_replicas: int = 3,
        master_replicas: int = 3,
        network_type: str = "OVNKubernetes"
    ) -> Dict[str, Any]:
        """Create a new OpenShift cluster on GCP

        The pull-secret is automatically read from ~/.config/openshift/pull-secret.json
        """

        # Check if cluster already exists
        if cluster_name in self.clusters:
            return {
                "success": False,
                "error": f"Cluster {cluster_name} already exists"
            }

        # Get pull-secret from secure location
        try:
            pull_secret = self._get_pull_secret()
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            return {
                "success": False,
                "error": str(e)
            }

        # Create cluster directory
        cluster_dir = os.path.join(self.base_dir, cluster_name)
        os.makedirs(cluster_dir, exist_ok=True)

        # Create install-config.yaml
        install_config = {
            "apiVersion": "v1",
            "baseDomain": base_domain,
            "metadata": {
                "name": cluster_name
            },
            "platform": {
                "gcp": {
                    "projectID": "openshift-dev-installer",
                    "region": region
                }
            },
            "pullSecret": pull_secret,
            "sshKey": ssh_public_key,
            "compute": [
                {
                    "architecture": "amd64",
                    "hyperthreading": "Enabled",
                    "name": "worker",
                    "platform": {},
                    "replicas": worker_replicas
                }
            ],
            "controlPlane": {
                "architecture": "amd64",
                "hyperthreading": "Enabled",
                "name": "master",
                "platform": {},
                "replicas": master_replicas
            },
            "networking": {
                "clusterNetwork": [
                    {
                        "cidr": "10.128.0.0/14",
                        "hostPrefix": 23
                    }
                ],
                "machineNetwork": [
                    {
                        "cidr": "10.0.0.0/16"
                    }
                ],
                "networkType": network_type,
                "serviceNetwork": [
                    "172.30.0.0/16"
                ]
            }
        }

        install_config_path = os.path.join(cluster_dir, "install-config.yaml")
        with open(install_config_path, 'w') as f:
            yaml.dump(install_config, f)

        logger.info(f"Created install-config.yaml for {cluster_name}")

        # Store cluster config
        cluster_config = ClusterConfig(
            name=cluster_name,
            region=region,
            base_domain=base_domain,
            worker_replicas=worker_replicas,
            master_replicas=master_replicas,
            network_type=network_type,
            cluster_dir=cluster_dir,
            status="creating"
        )
        self.clusters[cluster_name] = cluster_config

        # Run openshift-install create cluster
        logger.info(f"Starting cluster creation for {cluster_name}...")
        returncode, stdout, stderr = await self.runner.run(
            ["openshift-install", "create", "cluster", "--dir", cluster_dir, "--log-level", "info"]
        )

        if returncode != 0:
            cluster_config.status = "failed"
            return {
                "success": False,
                "cluster_name": cluster_name,
                "error": "Cluster creation failed. Check the .openshift_install.log file in the cluster directory for details."
            }

        # Update cluster config with kubeconfig path
        kubeconfig_path = os.path.join(cluster_dir, "auth", "kubeconfig")
        cluster_config.kubeconfig_path = kubeconfig_path
        cluster_config.status = "created"

        return {
            "success": True,
            "cluster_name": cluster_name,
            "cluster_dir": cluster_dir,
            "kubeconfig_path": kubeconfig_path,
            "message": f"Cluster {cluster_name} created successfully"
        }

    async def delete_cluster(self, cluster_name: str) -> Dict[str, Any]:
        """Delete an OpenShift cluster"""

        if cluster_name not in self.clusters:
            return {
                "success": False,
                "error": f"Cluster {cluster_name} not found"
            }

        cluster = self.clusters[cluster_name]
        cluster.status = "deleting"

        logger.info(f"Deleting cluster {cluster_name}...")
        returncode, stdout, stderr = await self.runner.run(
            ["openshift-install", "destroy", "cluster", "--dir", cluster.cluster_dir, "--log-level", "info"]
        )

        if returncode != 0:
            cluster.status = "created"  # Revert status
            return {
                "success": False,
                "cluster_name": cluster_name,
                "error": "Cluster deletion failed. Check the .openshift_install.log file in the cluster directory for details."
            }

        # Remove cluster directory
        if os.path.exists(cluster.cluster_dir):
            shutil.rmtree(cluster.cluster_dir)

        # Remove from clusters dict
        del self.clusters[cluster_name]

        return {
            "success": True,
            "cluster_name": cluster_name,
            "message": f"Cluster {cluster_name} deleted successfully"
        }

    async def get_cluster_status(self, cluster_name: str) -> Dict[str, Any]:
        """Get the status of a cluster"""

        if cluster_name not in self.clusters:
            return {
                "success": False,
                "error": f"Cluster {cluster_name} not found"
            }

        cluster = self.clusters[cluster_name]

        # Check if kubeconfig exists and cluster is accessible
        if cluster.kubeconfig_path and os.path.exists(cluster.kubeconfig_path):
            returncode, stdout, stderr = await self.runner.run(
                ["kubectl", "--kubeconfig", cluster.kubeconfig_path, "get", "nodes"]
            )

            accessible = returncode == 0
            nodes_info = stdout if returncode == 0 else stderr
        else:
            accessible = False
            nodes_info = "Kubeconfig not available"

        return {
            "success": True,
            "cluster_name": cluster.name,
            "status": cluster.status,
            "cluster_dir": cluster.cluster_dir,
            "kubeconfig_path": cluster.kubeconfig_path,
            "accessible": accessible,
            "nodes": nodes_info,
            "region": cluster.region,
            "base_domain": cluster.base_domain
        }

    async def list_clusters(self) -> Dict[str, Any]:
        """List all clusters"""

        clusters_info = []
        for cluster in self.clusters.values():
            clusters_info.append({
                "name": cluster.name,
                "status": cluster.status,
                "cluster_dir": cluster.cluster_dir,
                "region": cluster.region,
                "kubeconfig_available": cluster.kubeconfig_path is not None
            })

        return {
            "success": True,
            "clusters": clusters_info,
            "count": len(clusters_info)
        }

    async def get_kubeconfig(self, cluster_name: str) -> Dict[str, Any]:
        """Get the kubeconfig for a cluster"""

        if cluster_name not in self.clusters:
            return {
                "success": False,
                "error": f"Cluster {cluster_name} not found"
            }

        cluster = self.clusters[cluster_name]

        if not cluster.kubeconfig_path or not os.path.exists(cluster.kubeconfig_path):
            return {
                "success": False,
                "error": f"Kubeconfig not available for cluster {cluster_name}"
            }

        with open(cluster.kubeconfig_path, 'r') as f:
            kubeconfig_content = f.read()

        return {
            "success": True,
            "cluster_name": cluster_name,
            "kubeconfig_path": cluster.kubeconfig_path,
            "kubeconfig_content": kubeconfig_content
        }

    async def cloud_prepare(
        self,
        cluster_name: str,
        cloud_provider: str = "gcp"
    ) -> Dict[str, Any]:
        """Prepare cloud infrastructure for Submariner using subctl cloud prepare

        This prepares the cloud infrastructure (firewall rules, security groups, etc.)
        required for Submariner to work. Must be run before joining clusters.

        Supported cloud providers: gcp, aws, azure, openstack
        """

        if cluster_name not in self.clusters:
            return {
                "success": False,
                "error": f"Cluster {cluster_name} not found"
            }

        cluster = self.clusters[cluster_name]

        if not cluster.kubeconfig_path or not os.path.exists(cluster.kubeconfig_path):
            return {
                "success": False,
                "error": f"Kubeconfig not available for cluster {cluster_name}"
            }

        # Check if subctl is installed
        returncode, stdout, stderr = await self.runner.run(["subctl", "version"])
        if returncode != 0:
            return {
                "success": False,
                "error": "subctl not found. Please install subctl from https://submariner.io"
            }

        logger.info(f"Running cloud prepare for {cluster_name} on {cloud_provider}")

        # Run subctl cloud prepare
        cmd = [
            "subctl", "cloud", "prepare",
            "--kubeconfig", cluster.kubeconfig_path
        ]

        # Add cloud provider specific flags if needed
        if cloud_provider.lower() != "gcp":
            # For other cloud providers, we might need additional flags
            # but for now, subctl auto-detects the cloud provider
            pass

        returncode, stdout, stderr = await self.runner.run(cmd)

        if returncode != 0:
            return {
                "success": False,
                "cluster_name": cluster_name,
                "cloud_provider": cloud_provider,
                "error": "Cloud prepare failed. Check cluster logs for details."
            }

        return {
            "success": True,
            "cluster_name": cluster_name,
            "cloud_provider": cloud_provider,
            "message": f"Cloud infrastructure prepared successfully for {cluster_name}"
        }

    async def rename_kubeconfig_context(
        self,
        cluster_name: str,
        new_context_name: Optional[str] = None,
        new_user_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Rename context and user in a cluster's kubeconfig to avoid conflicts when merging

        This is useful when you want to merge multiple kubeconfigs that have the same
        context or user names (like "admin"). By default, it will use cluster_name-admin
        for both context and user names.
        """

        if cluster_name not in self.clusters:
            return {
                "success": False,
                "error": f"Cluster {cluster_name} not found"
            }

        cluster = self.clusters[cluster_name]

        if not cluster.kubeconfig_path or not os.path.exists(cluster.kubeconfig_path):
            return {
                "success": False,
                "error": f"Kubeconfig not available for cluster {cluster_name}"
            }

        # Set defaults if not provided
        if not new_context_name:
            new_context_name = f"{cluster_name}-admin"
        if not new_user_name:
            new_user_name = f"{cluster_name}-admin"

        try:
            # Read the kubeconfig
            with open(cluster.kubeconfig_path, 'r') as f:
                kubeconfig = yaml.safe_load(f)

            # Get old context and user names
            old_context_name = kubeconfig.get('current-context', '')

            # Find the old context to get the old user name
            old_user_name = None
            for ctx in kubeconfig.get('contexts', []):
                if ctx['name'] == old_context_name:
                    old_user_name = ctx['context'].get('user', '')
                    break

            if not old_user_name:
                return {
                    "success": False,
                    "error": "Could not determine current user from kubeconfig"
                }

            # Rename context
            for ctx in kubeconfig.get('contexts', []):
                if ctx['name'] == old_context_name:
                    ctx['name'] = new_context_name
                    ctx['context']['user'] = new_user_name

            # Rename user
            for user in kubeconfig.get('users', []):
                if user['name'] == old_user_name:
                    user['name'] = new_user_name

            # Update current-context
            kubeconfig['current-context'] = new_context_name

            # Write back the kubeconfig
            with open(cluster.kubeconfig_path, 'w') as f:
                yaml.dump(kubeconfig, f, default_flow_style=False)

            logger.info(f"Renamed kubeconfig context from '{old_context_name}' to '{new_context_name}' "
                       f"and user from '{old_user_name}' to '{new_user_name}' for cluster {cluster_name}")

            return {
                "success": True,
                "cluster_name": cluster_name,
                "old_context_name": old_context_name,
                "new_context_name": new_context_name,
                "old_user_name": old_user_name,
                "new_user_name": new_user_name,
                "message": f"Successfully renamed context and user in kubeconfig for {cluster_name}"
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to rename kubeconfig: {str(e)}"
            }

    async def merge_kubeconfigs(
        self,
        cluster_names: List[str],
        output_path: str = "/tmp/merged-kubeconfig.yaml"
    ) -> Dict[str, Any]:
        """Merge kubeconfigs from multiple clusters into a single file

        This is useful for managing multiple clusters from a single kubeconfig file.
        Before merging, ensure contexts and users have unique names (use rename_kubeconfig_context).
        """

        if not cluster_names:
            return {
                "success": False,
                "error": "No cluster names provided"
            }

        # Verify all clusters exist and have kubeconfigs
        kubeconfig_paths = []
        for cluster_name in cluster_names:
            if cluster_name not in self.clusters:
                return {
                    "success": False,
                    "error": f"Cluster {cluster_name} not found"
                }

            cluster = self.clusters[cluster_name]
            if not cluster.kubeconfig_path or not os.path.exists(cluster.kubeconfig_path):
                return {
                    "success": False,
                    "error": f"Kubeconfig not available for cluster {cluster_name}"
                }

            kubeconfig_paths.append(cluster.kubeconfig_path)

        # Use kubectl to merge kubeconfigs
        env = os.environ.copy()
        env['KUBECONFIG'] = ':'.join(kubeconfig_paths)

        # Create merged config
        returncode, stdout, stderr = await self.runner.run(
            ["kubectl", "config", "view", "--flatten"],
            capture_output=True
        )

        if returncode != 0:
            return {
                "success": False,
                "error": f"Failed to merge kubeconfigs: {stderr}"
            }

        # Write merged config to output file
        try:
            with open(output_path, 'w') as f:
                f.write(stdout)

            logger.info(f"Merged {len(cluster_names)} kubeconfigs to {output_path}")

            return {
                "success": True,
                "cluster_names": cluster_names,
                "output_path": output_path,
                "message": f"Successfully merged {len(cluster_names)} kubeconfigs to {output_path}"
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to write merged kubeconfig: {str(e)}"
            }

    async def deploy_submariner_full(
        self,
        cluster1_name: str,
        cluster2_name: str,
        project_id: str = "mw-integration-submariner",
        region: str = "us-east1",
        base_domain: str = "appsvc.gcp.subm.red-chesterfield.com"
    ) -> Dict[str, Any]:
        """One-command full Submariner deployment: creates 2 clusters and deploys Submariner

        This automated flow:
        1. Creates install-config files for both clusters with non-overlapping CIDRs
        2. Creates both clusters in parallel
        3. Runs cloud prepare on both clusters
        4. Deploys Submariner broker on cluster-1
        5. Joins both clusters to Submariner (auto-confirmed)
        6. Verifies connectivity
        7. Returns comprehensive status

        No user interaction required - fully automated!
        The pull-secret is automatically read from ~/.config/openshift/pull-secret.json
        """

        logger.info(f"Starting full Submariner deployment for {cluster1_name} and {cluster2_name}")

        # Get pull-secret from secure location
        try:
            pull_secret = self._get_pull_secret()
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            return {
                "success": False,
                "error": str(e)
            }

        # Create cluster directories
        cluster1_dir = os.path.join(self.base_dir, cluster1_name)
        cluster2_dir = os.path.join(self.base_dir, cluster2_name)
        os.makedirs(cluster1_dir, exist_ok=True)
        os.makedirs(cluster2_dir, exist_ok=True)

        # Create install-config for cluster 1 (non-overlapping CIDRs)
        install_config1 = {
            "additionalTrustBundlePolicy": "Proxyonly",
            "apiVersion": "v1",
            "baseDomain": base_domain,
            "compute": [
                {
                    "architecture": "amd64",
                    "hyperthreading": "Enabled",
                    "name": "worker",
                    "platform": {},
                    "replicas": 3
                }
            ],
            "controlPlane": {
                "architecture": "amd64",
                "hyperthreading": "Enabled",
                "name": "master",
                "platform": {},
                "replicas": 3
            },
            "metadata": {
                "creationTimestamp": None,
                "name": cluster1_name
            },
            "networking": {
                "clusterNetwork": [
                    {
                        "cidr": "10.128.0.0/14",
                        "hostPrefix": 23
                    }
                ],
                "machineNetwork": [
                    {
                        "cidr": "10.0.0.0/16"
                    }
                ],
                "networkType": "OVNKubernetes",
                "serviceNetwork": [
                    "172.30.0.0/16"
                ]
            },
            "platform": {
                "gcp": {
                    "projectID": project_id,
                    "region": region
                }
            },
            "publish": "External",
            "pullSecret": pull_secret
        }

        # Create install-config for cluster 2 (non-overlapping CIDRs)
        install_config2 = {
            "additionalTrustBundlePolicy": "Proxyonly",
            "apiVersion": "v1",
            "baseDomain": base_domain,
            "compute": [
                {
                    "architecture": "amd64",
                    "hyperthreading": "Enabled",
                    "name": "worker",
                    "platform": {},
                    "replicas": 3
                }
            ],
            "controlPlane": {
                "architecture": "amd64",
                "hyperthreading": "Enabled",
                "name": "master",
                "platform": {},
                "replicas": 3
            },
            "metadata": {
                "creationTimestamp": None,
                "name": cluster2_name
            },
            "networking": {
                "clusterNetwork": [
                    {
                        "cidr": "10.132.0.0/14",
                        "hostPrefix": 23
                    }
                ],
                "machineNetwork": [
                    {
                        "cidr": "10.0.0.0/16"
                    }
                ],
                "networkType": "OVNKubernetes",
                "serviceNetwork": [
                    "172.31.0.0/16"
                ]
            },
            "platform": {
                "gcp": {
                    "projectID": project_id,
                    "region": region
                }
            },
            "publish": "External",
            "pullSecret": pull_secret
        }

        # Write install-config files
        with open(os.path.join(cluster1_dir, "install-config.yaml"), 'w') as f:
            yaml.dump(install_config1, f, default_flow_style=False)
        with open(os.path.join(cluster2_dir, "install-config.yaml"), 'w') as f:
            yaml.dump(install_config2, f, default_flow_style=False)

        logger.info("Created install-config files with non-overlapping CIDRs")

        # Create both clusters in parallel
        logger.info("Creating both clusters in parallel...")
        cluster1_task = asyncio.create_task(
            self.runner.run(
                ["openshift-install", "create", "cluster", "--dir", cluster1_dir, "--log-level", "info"]
            )
        )
        cluster2_task = asyncio.create_task(
            self.runner.run(
                ["openshift-install", "create", "cluster", "--dir", cluster2_dir, "--log-level", "info"]
            )
        )

        cluster1_result, cluster2_result = await asyncio.gather(cluster1_task, cluster2_task)

        if cluster1_result[0] != 0:
            return {
                "success": False,
                "error": f"Cluster {cluster1_name} creation failed. Check the .openshift_install.log file in the cluster directory."
            }

        if cluster2_result[0] != 0:
            return {
                "success": False,
                "error": f"Cluster {cluster2_name} creation failed. Check the .openshift_install.log file in the cluster directory."
            }

        logger.info("Both clusters created successfully!")

        # Update cluster configs
        kubeconfig1 = os.path.join(cluster1_dir, "auth", "kubeconfig")
        kubeconfig2 = os.path.join(cluster2_dir, "auth", "kubeconfig")
        metadata1 = os.path.join(cluster1_dir, "metadata.json")
        metadata2 = os.path.join(cluster2_dir, "metadata.json")

        self.clusters[cluster1_name] = ClusterConfig(
            name=cluster1_name,
            cluster_dir=cluster1_dir,
            kubeconfig_path=kubeconfig1,
            status="created"
        )
        self.clusters[cluster2_name] = ClusterConfig(
            name=cluster2_name,
            cluster_dir=cluster2_dir,
            kubeconfig_path=kubeconfig2,
            status="created"
        )

        # AUTOMATICALLY rename kubeconfig contexts to avoid conflicts
        logger.info("Renaming kubeconfig contexts to avoid conflicts...")
        rename1_result = await self.rename_kubeconfig_context(cluster1_name)
        rename2_result = await self.rename_kubeconfig_context(cluster2_name)

        if not rename1_result.get("success") or not rename2_result.get("success"):
            return {
                "success": False,
                "error": "Failed to rename kubeconfig contexts",
                "rename1_result": rename1_result,
                "rename2_result": rename2_result
            }

        logger.info(f"Renamed contexts: {rename1_result.get('new_context_name')} and {rename2_result.get('new_context_name')}")

        # Run cloud prepare on both clusters in parallel
        logger.info("Running cloud prepare on both clusters...")

        # Set GCP credentials environment variable
        env = os.environ.copy()
        gcp_creds = os.path.expanduser("~/.gcp/osServiceAccount.json")
        if os.path.exists(gcp_creds):
            env['GOOGLE_APPLICATION_CREDENTIALS'] = gcp_creds

        prepare1_task = asyncio.create_task(
            self.runner.run(
                ["subctl", "cloud", "prepare", "gcp", "--ocp-metadata", metadata1, "--kubeconfig", kubeconfig1],
                cwd=cluster1_dir
            )
        )
        prepare2_task = asyncio.create_task(
            self.runner.run(
                ["subctl", "cloud", "prepare", "gcp", "--ocp-metadata", metadata2, "--kubeconfig", kubeconfig2],
                cwd=cluster2_dir
            )
        )

        prepare1_result, prepare2_result = await asyncio.gather(prepare1_task, prepare2_task)

        if prepare1_result[0] != 0 or prepare2_result[0] != 0:
            return {
                "success": False,
                "error": "Cloud prepare failed. Check cluster logs for details."
            }

        logger.info("Cloud prepare completed on both clusters")

        # Deploy broker on cluster 1
        logger.info("Deploying Submariner broker on cluster-1...")
        broker_result = await self.runner.run(
            ["subctl", "deploy-broker", "--kubeconfig", kubeconfig1],
            cwd=cluster1_dir
        )

        if broker_result[0] != 0:
            return {
                "success": False,
                "error": "Broker deployment failed. Check cluster logs for details."
            }

        logger.info("Broker deployed successfully")

        # Find broker-info.subm file
        broker_info = os.path.join(cluster1_dir, "broker-info.subm")
        if not os.path.exists(broker_info):
            # Try current directory
            broker_info = os.path.join(self.base_dir, "broker-info.subm")

        if not os.path.exists(broker_info):
            return {
                "success": False,
                "error": "broker-info.subm file not found after broker deployment"
            }

        # Join cluster 1 (with auto-yes to avoid gateway selection prompt)
        logger.info("Joining cluster-1 to Submariner...")
        join1_proc = await asyncio.create_subprocess_exec(
            "bash", "-c", f"echo yes | subctl join {broker_info} --clusterid cluster-1 --kubeconfig {kubeconfig1}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cluster1_dir
        )
        join1_stdout, join1_stderr = await join1_proc.communicate()

        if join1_proc.returncode != 0:
            return {
                "success": False,
                "error": "Cluster-1 join failed. Check cluster logs for details."
            }

        logger.info("Cluster-1 joined successfully")

        # Join cluster 2 (with auto-yes to avoid gateway selection prompt)
        logger.info("Joining cluster-2 to Submariner...")
        join2_proc = await asyncio.create_subprocess_exec(
            "bash", "-c", f"echo yes | subctl join {broker_info} --clusterid cluster-2 --kubeconfig {kubeconfig2}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cluster2_dir
        )
        join2_stdout, join2_stderr = await join2_proc.communicate()

        if join2_proc.returncode != 0:
            return {
                "success": False,
                "error": "Cluster-2 join failed. Check cluster logs for details."
            }

        logger.info("Cluster-2 joined successfully")

        # Wait a bit for connections to establish
        await asyncio.sleep(5)

        # Verify connectivity
        logger.info("Verifying Submariner connectivity...")
        verify_result = await self.runner.run(
            ["subctl", "show", "connections", "--kubeconfig", kubeconfig1]
        )

        verify_result2 = await self.runner.run(
            ["subctl", "show", "connections", "--kubeconfig", kubeconfig2]
        )

        return {
            "success": True,
            "cluster1_name": cluster1_name,
            "cluster2_name": cluster2_name,
            "cluster1_kubeconfig": kubeconfig1,
            "cluster2_kubeconfig": kubeconfig2,
            "cluster1_dir": cluster1_dir,
            "cluster2_dir": cluster2_dir,
            "message": "Submariner deployment complete! Both clusters are connected.",
            "cluster1_connections": verify_result[1],
            "cluster2_connections": verify_result2[1],
            "broker_info_path": broker_info
        }


# Create MCP server
app = Server("gcp-openshift-mcp")
manager = GCPOpenShiftManager()


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available GCP OpenShift management tools"""
    return [
        Tool(
            name="check_prerequisites",
            description="Check if required tools (openshift-install, gcloud, kubectl) are installed and available.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="create_cluster",
            description="Create a new OpenShift cluster on GCP with a custom name. This will run openshift-install to provision the cluster infrastructure. The pull-secret is automatically read from ~/.config/openshift/pull-secret.json (setup required on first use).",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Unique name for the cluster (e.g., 'submariner-gcp-1')"
                    },
                    "ssh_public_key": {
                        "type": "string",
                        "description": "SSH public key for cluster access"
                    },
                    "region": {
                        "type": "string",
                        "description": "GCP region for the cluster",
                        "default": "us-east1"
                    },
                    "base_domain": {
                        "type": "string",
                        "description": "Base domain for the cluster",
                        "default": "devcluster.openshift.com"
                    },
                    "worker_replicas": {
                        "type": "integer",
                        "description": "Number of worker nodes",
                        "default": 3
                    },
                    "master_replicas": {
                        "type": "integer",
                        "description": "Number of master nodes",
                        "default": 3
                    },
                    "network_type": {
                        "type": "string",
                        "description": "Network plugin type",
                        "default": "OVNKubernetes"
                    }
                },
                "required": ["cluster_name", "ssh_public_key"]
            }
        ),
        Tool(
            name="delete_cluster",
            description="Delete an OpenShift cluster and clean up all GCP resources using openshift-install destroy.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster to delete"
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="get_cluster_status",
            description="Get detailed status of a cluster including accessibility, nodes, and configuration.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster"
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="list_clusters",
            description="List all GCP OpenShift clusters managed by this server.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="get_kubeconfig",
            description="Get the kubeconfig file content for a cluster. This can be used with the Submariner MCP server.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster"
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="cloud_prepare",
            description="Prepare cloud infrastructure for Submariner using 'subctl cloud prepare'. This MUST be run before joining clusters to Submariner. It configures firewall rules, security groups, and other cloud-specific requirements for GCP, AWS, Azure, and OpenStack.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster to prepare"
                    },
                    "cloud_provider": {
                        "type": "string",
                        "description": "Cloud provider type",
                        "enum": ["gcp", "aws", "azure", "openstack"],
                        "default": "gcp"
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="rename_kubeconfig_context",
            description="Rename context and user in a cluster's kubeconfig to avoid conflicts when merging multiple kubeconfigs. This is essential when merging kubeconfigs that have the same context/user names (like 'admin'). By default, uses '{cluster_name}-admin' for both.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster whose kubeconfig to rename"
                    },
                    "new_context_name": {
                        "type": "string",
                        "description": "New context name (optional, defaults to '{cluster_name}-admin')"
                    },
                    "new_user_name": {
                        "type": "string",
                        "description": "New user name (optional, defaults to '{cluster_name}-admin')"
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="merge_kubeconfigs",
            description="Merge kubeconfigs from multiple clusters into a single file. Before merging, ensure contexts and users have unique names using rename_kubeconfig_context. This allows you to manage multiple clusters from a single kubeconfig file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of cluster names to merge"
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Path where the merged kubeconfig should be saved",
                        "default": "/tmp/merged-kubeconfig.yaml"
                    }
                },
                "required": ["cluster_names"]
            }
        ),
        Tool(
            name="deploy_submariner_full",
            description="ONE-COMMAND FULL DEPLOYMENT: Creates 2 OpenShift clusters on GCP and deploys Submariner with complete multi-cluster networking. Fully automated - no user interaction required! Includes: cluster creation (parallel), automatic kubeconfig context renaming, cloud prepare, broker deployment, cluster joining (auto-confirmed), and connectivity verification. Uses proven configuration with non-overlapping CIDRs. The pull-secret is automatically read from ~/.config/openshift/pull-secret.json (setup required on first use).",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster1_name": {
                        "type": "string",
                        "description": "Name for the first cluster (e.g., 'submariner-gcp-1')"
                    },
                    "cluster2_name": {
                        "type": "string",
                        "description": "Name for the second cluster (e.g., 'submariner-gcp-2')"
                    },
                    "project_id": {
                        "type": "string",
                        "description": "GCP project ID",
                        "default": "mw-integration-submariner"
                    },
                    "region": {
                        "type": "string",
                        "description": "GCP region for both clusters",
                        "default": "us-east1"
                    },
                    "base_domain": {
                        "type": "string",
                        "description": "Base domain for the clusters",
                        "default": "appsvc.gcp.subm.red-chesterfield.com"
                    }
                },
                "required": ["cluster1_name", "cluster2_name"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls"""
    try:
        if name == "check_prerequisites":
            result = await manager.check_prerequisites()
        elif name == "create_cluster":
            result = await manager.create_cluster(**arguments)
        elif name == "delete_cluster":
            result = await manager.delete_cluster(**arguments)
        elif name == "get_cluster_status":
            result = await manager.get_cluster_status(**arguments)
        elif name == "list_clusters":
            result = await manager.list_clusters()
        elif name == "get_kubeconfig":
            result = await manager.get_kubeconfig(**arguments)
        elif name == "cloud_prepare":
            result = await manager.cloud_prepare(**arguments)
        elif name == "rename_kubeconfig_context":
            result = await manager.rename_kubeconfig_context(**arguments)
        elif name == "merge_kubeconfigs":
            result = await manager.merge_kubeconfigs(**arguments)
        elif name == "deploy_submariner_full":
            result = await manager.deploy_submariner_full(**arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(
            type="text",
            text=json.dumps(result, indent=2)
        )]
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e)}, indent=2)
        )]


async def main():
    """Run the MCP server"""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
