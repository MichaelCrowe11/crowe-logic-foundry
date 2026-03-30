from setuptools import setup, find_packages
from config.agent_config import AGENT_VERSION

setup(
    name="crowe-logic",
    version=AGENT_VERSION,
    packages=find_packages(),
    install_requires=[
        "azure-ai-agents>=1.1.0",
        "azure-identity>=1.25.0",
        "azure-ai-projects>=2.0.0",
        "opentelemetry-sdk>=1.40.0",
        "azure-core-tracing-opentelemetry>=1.0.0b12",
        "click>=8.1.0",
        "rich>=14.0.0",
        "prompt-toolkit>=3.0.0",
        "python-dotenv>=1.0.0",
        "httpx>=0.28.0",
        "beautifulsoup4>=4.12.0",
    ],
    entry_points={
        "console_scripts": [
            "crowe-logic=cli.crowe_logic:main",
        ],
    },
    author="Michael Crowe",
    description="Crowe Logic — Universal AI Agent powered by gpt-oss-120b on Azure AI Foundry",
)
