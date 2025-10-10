# main.py
import sys, os
sys.path.append(os.path.abspath(".llm_agent-utilities"))
from llm_agent_utilities import load_agent

def run_agent(agent_yaml: str, input_payload: dict):
    """Generic agent runner."""
    agent = load_agent(agent_yaml)
    return agent.run(input_payload)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python main.py <agent_yaml> <input_path>")
        sys.exit(1)
    agent_yaml, input_path = sys.argv[1], sys.argv[2]
    result = run_agent(agent_yaml, {"image_folder": input_path})
    print(result)
