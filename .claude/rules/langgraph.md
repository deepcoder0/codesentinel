When working with LangGraph:
- Always use TypedDict (not Pydantic) for graph state
- Use Annotated types with reducer functions for state fields that multiple nodes write to: `Annotated[list, operator.add]`
- Nodes are plain functions: `def node_name(state: ReviewState) -> dict` — return a partial dict to merge
- Never mutate state directly inside a node — always return the update
- Use `add_conditional_edges` for branching logic, not if/else inside nodes
- For parallel execution, use `Send()` API — not threading or asyncio
- Test graphs by calling `graph.invoke(initial_state)` and asserting on the final state
