import json
import subprocess
import sys

REGION = "ap-south-1"
CLUSTER = "enma-prod"
SERVICE = "enma-gateway"
NEW_URL = "http://13.201.225.186:8000"

def run_cmd(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running '{cmd}': {result.stderr}")
        sys.exit(1)
    return result.stdout

# 1. Fetch current TD
print("Fetching current task definition...")
td_str = run_cmd(f"aws ecs describe-task-definition --task-definition {SERVICE} --region {REGION} --query taskDefinition --output json")
td = json.loads(td_str)

# 2. Clean up TD for registration
keys_to_remove = ["taskDefinitionArn", "revision", "status", "requiresAttributes", "compatibilities", "registeredAt", "registeredBy"]
for key in keys_to_remove:
    td.pop(key, None)

# 3. Update the BACKEND_URL in container definitions
updated = False
for container in td.get("containerDefinitions", []):
    for env in container.get("environment", []):
        if env.get("name") == "BACKEND_URL":
            old_url = env.get("value")
            env["value"] = NEW_URL
            print(f"Updated BACKEND_URL from {old_url} to {NEW_URL}")
            updated = True

if not updated:
    print("Could not find BACKEND_URL in environment variables.")
    sys.exit(1)

# 4. Save and register new TD
with open("new_td.json", "w") as f:
    json.dump(td, f, indent=2)

print("Registering new task definition...")
reg_out = run_cmd(f"aws ecs register-task-definition --cli-input-json file://new_td.json --region {REGION}")
reg_data = json.loads(reg_out)
new_revision = reg_data["taskDefinition"]["revision"]
new_arn = reg_data["taskDefinition"]["taskDefinitionArn"]
print(f"Registered new revision: {new_revision} ({new_arn})")

# 5. Update service
print(f"Updating ECS service {SERVICE} to use revision {new_revision}...")
run_cmd(f"aws ecs update-service --cluster {CLUSTER} --service {SERVICE} --task-definition {SERVICE}:{new_revision} --region {REGION}")
print("Service updated successfully. The gateway will restart with the new IP.")
