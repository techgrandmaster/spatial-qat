#!/usr/bin/env python3
"""
Seed the demo board with pre-configured sample data.

This script creates a sample workspace with pre-loaded assets and components
so the demo board is immediately available without requiring live API calls.
Run after database is initialized:
    uv run python scripts/seed_demo.py
"""

import asyncio
import logging
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default backend URL
BASE_URL = "http://localhost:8000/api/v1"


async def seed_demo(base_url: str = BASE_URL) -> None:
    """Create a sample workspace and trigger analysis to warm the cache."""

    async with httpx.AsyncClient(timeout=120) as client:
        # Step 1: Create guest session
        logger.info("Creating guest session...")
        resp = await client.post(f"{base_url}/sessions")
        resp.raise_for_status()
        session = resp.json()
        token = session["token"]
        headers = {"Authorization": f"Bearer {token}"}
        logger.info(f"Session created: {session['id'][:8]}...")

        # Step 2: Create workspace
        logger.info("Creating sample workspace...")
        resp = await client.post(
            f"{base_url}/workspaces",
            headers=headers,
            json={"name": "Arduino Uno R3 Demo Board", "domain": "electronics"},
        )
        resp.raise_for_status()
        workspace = resp.json()
        workspace_id = workspace["id"]
        logger.info(f"Workspace created: {workspace_id[:8]}...")

        # Step 3: Upload board image if available
        data_dir = Path(__file__).resolve().parent.parent.parent / "data" / "boards"
        image_files = list(data_dir.glob("*.jpg")) + list(data_dir.glob("*.png")) + list(data_dir.glob("*.webp"))

        if image_files:
            image_file = sorted(image_files)[0]
            logger.info(f"Uploading board image: {image_file.name}")
            async with image_file.open("rb") as f:
                resp = await client.post(
                    f"{base_url}/workspaces/{workspace_id}/assets",
                    headers=headers,
                    files={"file": (image_file.name, f, "image/jpeg")},
                )
                resp.raise_for_status()
                logger.info(f"Image uploaded: {resp.json()['id'][:8]}...")
        else:
            logger.warning(f"No board images found in {data_dir}. Skipping image upload.")

        # Step 4: Upload datasheet PDF if available
        datasheet_dir = data_dir.parent / "datasheets"
        pdf_files = list(datasheet_dir.glob("*.pdf"))

        if pdf_files:
            pdf_file = sorted(pdf_files)[0]
            logger.info(f"Uploading datasheet: {pdf_file.name}")
            async with pdf_file.open("rb") as f:
                resp = await client.post(
                    f"{base_url}/workspaces/{workspace_id}/assets",
                    headers=headers,
                    files={"file": (pdf_file.name, f, "application/pdf")},
                )
                resp.raise_for_status()
                logger.info(f"PDF uploaded: {resp.json()['id'][:8]}...")
        else:
            logger.warning(f"No datasheets found in {datasheet_dir}. Skipping PDF upload.")

        # Step 5: Trigger board analysis to warm cache
        logger.info("Triggering board analysis (this warms the SHA-256 cache)...")
        resp = await client.post(
            f"{base_url}/workspaces/{workspace_id}/analyze",
            headers=headers,
        )
        resp.raise_for_status()
        run = resp.json()
        logger.info(f"Analysis run started: {run['id'][:8]}...")

        # Step 6: Wait for analysis to complete
        logger.info("Waiting for analysis to complete...")
        run_id = run["id"]
        for _ in range(60):  # Wait up to 60 seconds
            await asyncio.sleep(1)
            resp = await client.get(f"{base_url}/runs/{run_id}/events", headers=headers)
            if resp.status_code == 200:
                logger.info("Analysis completed!")
                break
        else:
            logger.warning("Analysis may still be running. Check /api/v1/runs/{run_id}/events")

        # Step 7: Generate assessment quiz
        logger.info("Generating assessment quiz...")
        resp = await client.post(
            f"{base_url}/workspaces/{workspace_id}/assessments",
            headers=headers,
            json={"n": 5, "difficulty": "medium"},
        )
        if resp.status_code == 200:
            assessment = resp.json()
            logger.info(f"Quiz generated: {assessment['id'][:8]}...")
        else:
            logger.warning(f"Quiz generation failed: {resp.status_code}")

        logger.info(f"\nDemo board ready! Workspace ID: {workspace_id}")
        logger.info("Access at: http://localhost:80 (or 3000 in dev mode)")


if __name__ == "__main__":
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else BASE_URL
    asyncio.run(seed_demo(url))
