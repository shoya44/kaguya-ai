"""Register Kaguya's read-only project inspection tools at Python startup.

The backend is launched with its working directory set to backend/, so Python's
standard site initialization imports this module automatically. Existing chat
tools are kept unchanged; only read-only project inspection is added.
"""
from app import project_inspector, tools

_original_run = tools.run
for declaration in project_inspector.DECLARATIONS:
    if not any(item.get('name') == declaration.get('name') for item in tools.DECLARATIONS):
        tools.DECLARATIONS.append(declaration)


async def _run_with_project_tools(name, args, memory, now=None):
    if name in {'project_status', 'project_search', 'project_read'}:
        return project_inspector.run(name, args)
    return await _original_run(name, args, memory, now)


tools.run = _run_with_project_tools
