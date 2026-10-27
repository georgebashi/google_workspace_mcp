"""Text results reach the client as plain text, not as a JSON-escaped copy."""

import importlib

import pytest
from fastmcp import Client
from mcp.types import TextContent

from core.server import SecureFastMCP, server

TOOL_MODULES = [
    "gmail.gmail_tools",
    "gdrive.drive_tools",
    "gcalendar.calendar_tools",
    "gdocs.docs_tools",
    "gsheets.sheets_tools",
    "gchat.chat_tools",
    "gforms.forms_tools",
    "gslides.slides_tools",
    "gtasks.tasks_tools",
    "gcontacts.contacts_tools",
    "gsearch.search_tools",
    "gappsscript.apps_script_tools",
]
for module in TOOL_MODULES:
    importlib.import_module(module)


def probe_server() -> SecureFastMCP:
    app = SecureFastMCP(name="output-schema")

    @app.tool
    def bare() -> str:
        return 'line one\nsays "hi"'

    @app.tool()
    def called() -> str:
        return "called"

    def direct() -> str:
        return "direct"

    app.tool(direct)

    @app.tool()
    def structured() -> dict:
        return {"count": 2}

    @app.tool(
        output_schema={"type": "object", "properties": {"n": {"type": "integer"}}}
    )
    def declared() -> dict:
        return {"n": 1}

    return app


@pytest.mark.asyncio
async def test_text_tools_publish_no_output_schema():
    async with Client(probe_server()) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    for name in ("bare", "called", "direct", "structured"):
        assert tools[name].output_schema is None, name
    assert tools["declared"].output_schema is not None


@pytest.mark.asyncio
async def test_text_result_is_sent_once_as_plain_text():
    async with Client(probe_server()) as client:
        result = await client.call_tool("bare", {})

    assert result.structured_content is None
    (block,) = result.content
    assert isinstance(block, TextContent)
    assert block.text == 'line one\nsays "hi"'


@pytest.mark.asyncio
async def test_dict_result_is_still_structured_without_a_wrapper():
    async with Client(probe_server()) as client:
        result = await client.call_tool("structured", {})

    assert result.structured_content == {"count": 2}


@pytest.mark.asyncio
async def test_no_registered_tool_declares_an_output_schema():
    tools = await server.list_tools(run_middleware=False)
    assert tools
    assert [tool.name for tool in tools if tool.output_schema is not None] == []
