# book-identifier-agent.py
from main import run_agent

def main():
    agent_yaml = "agent-yamls/book_identifier.yaml"
    input_payload = {"image_folder": "images"}
    result = run_agent(agent_yaml, input_payload)
    print(result)

if __name__ == "__main__":
    main()
