```typescript
function agent_loop(goal):
    context = [goal]
    tools = register_builtin_tools()
    while True:
        text, action = LLM(context,tools)
        context += message("assistant",[text,action]) 
        if action.type == "done":
            return text
        if action.type == "call_tool":
            result = run_tool(tools[action.tool],action.args)
            context += message("user",result)
```

```typescript
# -------------- 包含上下文工程 ------------------- #
# 先装配Agent
function build_agent(project):
    system_prompt = vendor_system_prompt()
    rules = load_rule_files('CLAUDE.md','AGENTS.md')
    tools = register_builtin_tools()
    memory = open_memory()
    retriever = open_retriever()
    hooks = register_hooks()
    guardrail = register_guardrail()
    sandbox = create_sandbox(limits)
    tracer = open_trace()
    return Harness(system_prompt,rules,tools,memory,retriever,hooks,guardrail,sandbox,tracer)


function agent_loop(goal,H):
    context = H.system_prompt
    context += H.rules
    context += H.memory.read(goal)
    context += H.retriever.read(goal)
    context += goal

    done = False
    answer = None
    steps = 0
    while not done and steps < MAX_STEPS:
        steps += 1 
        if token_count(context) > LIMIT:
            context = compact(context)
        menu = H.tools.list()  # 工具在compact之后加载

        text, action = LLM(context,menu)
        context += message('assistant',[text,action])

        H.trace.record(text,action) #可观测性

        if action.type == 'done':
            done = True
            answer = text
            continue

        if not H.guardrail.allow(action):
            context += message('user',"该动作被策略拦截")  # 或者人工审批
            continue

        verdict = run_hooks(H.hooks, "Pretool Use", action)
        
        if action.type == 'call_tool':
            result = run_tool(H.tools[action.tool],action.args)
            context += message("user",result)
        elif action.type == 'take_note':
            H.memory.write(action.note)
    
    H.memory.consolidate(context)
    return answer 
```
