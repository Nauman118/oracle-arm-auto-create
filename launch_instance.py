#!/usr/bin/env python3
import os
import sys
import time
import oci
from oci.core import ComputeClient, VirtualNetworkClient
from oci.identity import IdentityClient
from oci.exceptions import ServiceError

def main():
    print("==================================================")
    print("🚀 OCI ALWAYS-FREE AMPERE A1 AUTO-CLAIM SCRIPT")
    print("==================================================")

    # 1. Read environment variables
    user_ocid = os.environ.get("OCI_USER_OCID")
    tenancy_ocid = os.environ.get("OCI_TENANCY_OCID")
    fingerprint = os.environ.get("OCI_FINGERPRINT")
    key_content = os.environ.get("OCI_KEY_CONTENT")
    region = os.environ.get("OCI_REGION", "us-ashburn-1")
    subnet_ocid = os.environ.get("OCI_SUBNET_OCID")
    ssh_public_key = os.environ.get("OCI_SSH_PUBLIC_KEY")

    if not all([user_ocid, tenancy_ocid, fingerprint, key_content, subnet_ocid]):
        print("❌ Error: Missing required environment variables.")
        print("Required: OCI_USER_OCID, OCI_TENANCY_OCID, OCI_FINGERPRINT, OCI_KEY_CONTENT, OCI_SUBNET_OCID")
        sys.exit(1)

    # Clean up key content
    key_content = key_content.strip()
    if not key_content.startswith("-----BEGIN"):
        key_content = f"-----BEGIN RSA PRIVATE KEY-----\n{key_content}\n-----END RSA PRIVATE KEY-----"

    config = {
        "user": user_ocid,
        "key_content": key_content,
        "fingerprint": fingerprint,
        "tenancy": tenancy_ocid,
        "region": region
    }

    try:
        oci.config.validate_config(config)
    except Exception as e:
        print(f"❌ Configuration validation failed: {e}")
        sys.exit(1)

    compute_client = ComputeClient(config)
    identity_client = IdentityClient(config)

    # 2. Check if instance already exists
    print("🔍 Checking existing instances in compartment...")
    try:
        instances = compute_client.list_instances(compartment_id=tenancy_ocid).data
        for inst in instances:
            if inst.display_name == "nauman-studio-vps" and inst.lifecycle_state in ["PROVISIONING", "RUNNING"]:
                print(f"🎉 INSTANCE ALREADY EXISTS AND IS {inst.lifecycle_state}!")
                print(f"Instance OCID: {inst.id}")
                sys.exit(0)
    except Exception as e:
        print(f"Warning checking instances: {e}")

    # 3. Get Availability Domains
    print(f"🌐 Fetching Availability Domains for region {region}...")
    try:
        ads = identity_client.list_availability_domains(compartment_id=tenancy_ocid).data
        ad_names = [ad.name for ad in ads]
        print(f"Found {len(ad_names)} ADs: {', '.join(ad_names)}")
    except Exception as e:
        print(f"❌ Failed to list ADs: {e}")
        sys.exit(1)

    # 4. Find latest Ubuntu 24.04 ARM image
    print("🔍 Searching for Canonical Ubuntu 24.04 ARM64 image...")
    image_ocid = None
    try:
        images = compute_client.list_images(
            compartment_id=tenancy_ocid,
            operating_system="Canonical Ubuntu",
            shape="VM.Standard.A1.Flex",
            sort_by="TIMECREATED",
            sort_order="DESC"
        ).data

        for img in images:
            if "24.04" in img.display_name and "aarch64" in img.display_name:
                image_ocid = img.id
                print(f"Found image: {img.display_name} ({image_ocid})")
                break
        
        # Fallback to any aarch64 ubuntu
        if not image_ocid and images:
            image_ocid = images[0].id
            print(f"Using fallback image: {images[0].display_name}")
    except Exception as e:
        print(f"Warning fetching image list: {e}")
        # Default fallback image for us-ashburn-1 Canonical Ubuntu 24.04 aarch64
        image_ocid = "ocid1.image.oc1.iad.aaaaaaaaioyy7je3vndsccly24frkfptl5lggvyupubg74awcf2gmua7k3ra"

    metadata = {}
    if ssh_public_key:
        metadata["ssh_authorized_keys"] = ssh_public_key.strip()

    # 5. Try launching instance across each Availability Domain
    for ad_name in ad_names:
        print(f"\n⚡ Attempting to create instance in: {ad_name}...")
        launch_details = oci.core.models.LaunchInstanceDetails(
            display_name="nauman-studio-vps",
            compartment_id=tenancy_ocid,
            availability_domain=ad_name,
            shape="VM.Standard.A1.Flex",
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=4.0,
                memory_in_gbs=24.0
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=image_ocid,
                boot_volume_size_in_gbs=150
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=subnet_ocid,
                assign_public_ip=True
            ),
            metadata=metadata
        )

        try:
            res = compute_client.launch_instance(launch_details)
            instance = res.data
            print("\n🎉🎉🎉 SUCCESS! SERVER CREATED! 🎉🎉🎉")
            print(f"Instance ID: {instance.id}")
            print(f"State: {instance.lifecycle_state}")
            print(f"Availability Domain: {instance.availability_domain}")
            sys.exit(0)
        except ServiceError as e:
            if "Out of host capacity" in str(e) or e.status == 500:
                print(f"⚠️ Out of host capacity in {ad_name}.")
            elif e.status == 429:
                print(f"⚠️ Rate limited (429) in {ad_name}. Backing off.")
            else:
                print(f"⚠️ Service error in {ad_name}: {e.message}")
        except Exception as e:
            print(f"Unexpected error in {ad_name}: {e}")

        time.sleep(3)

    print("\n⏳ Capacity not yet free in this run. Next scheduled attempt will retry automatically.")
    sys.exit(0)

if __name__ == "__main__":
    main()
