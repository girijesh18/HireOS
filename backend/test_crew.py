import asyncio
from crewai import Agent, Task, Crew, Process
from crewai.tools import tool
import os
from dotenv import load_dotenv

load_dotenv()

@tool("Simple Scraper")
def simple_scraper(url: str) -> str:
    """Scrapes text from a url."""
    return "This is a fake webpage for a Senior Python Engineer at OpenAI. Salary 200k-300k. Remote."

def run():
    agent = Agent(
        role="Job Researcher",
        goal="Extract job details into JSON.",
        backstory="Expert at parsing jobs.",
        tools=[simple_scraper],
        llm="gemini/gemini-1.5-flash",
        verbose=True,
        allow_delegation=False
    )
    
    task = Task(
        description="Scrape https://fake.com and return JSON with company, title, remote, salary_min.",
        expected_output="JSON only",
        agent=agent
    )
    
    crew = Crew(agents=[agent], tasks=[task], verbose=True)
    res = crew.kickoff()
    print("Result:", res.raw if hasattr(res, 'raw') else res)

run()
