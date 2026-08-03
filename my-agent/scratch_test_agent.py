import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent import root_agent
from google.adk.agents.context import Context

async def main():
    ctx = Context()
    async for event in root_agent.run(ctx=ctx, node_input="Run the marksheet pipeline for student RAJA THAAVANESHWARA"):
        print("Event:", event)

if __name__ == "__main__":
    asyncio.run(main())
