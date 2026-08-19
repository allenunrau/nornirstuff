import os
from nornir import InitNornir
from nornir_netmiko.tasks import netmiko_send_command
from nornir.core.task import Task, Result

# Define the list of commands to run across hosts
COMMANDS = [
    "no page",
    "show running-config"
]

def run_commands_and_save(task: Task) -> Result:
    """Custom task to run multiple commands and write results to a per-host file."""
    host_output = []
    
    # Iterate through each command in the list
    for cmd in COMMANDS:
        # Run the netmiko task for the current command
        cmd_result = task.run(
            task=netmiko_send_command, 
            command_string=cmd
        )
        
        # Format the output header and append the command results
        host_output.append(f"=== Command: {cmd} ===")
        host_output.append(cmd_result.result)
        host_output.append("\n" + "="*40 + "\n")
    
    # Combine all results into a single string block
    final_output = "\n".join(host_output)
    
    # Ensure an output directory exists to avoid FileNotFoundError
    os.makedirs("outputs", exist_ok=True)
    
    # Write output to an individual text file using the unique host name
    filename = f"outputs/{task.host.name}_config.aos"
    with open(filename, "w", encoding="utf-8") as file:
        file.write(final_output)
        
    return Result(
        host=task.host,
        result=f"Successfully saved all command outputs to {filename}"
    )

def main():
    # Initialize Nornir using your inventory config file
    nr = InitNornir(config_file="config.yaml")
    
    # Execute the custom task across all hosts in parallel
    results = nr.run(task=run_commands_and_save)
    
    # Check for any failures during execution
    for host_name, host_result in results.items():
        if host_result.failed:
            print(f"❌ Host {host_name} failed: {host_result.exception}")
        else:
            print(f"✅ Host {host_name}: {host_result.result}")

if __name__ == "__main__":
    main()