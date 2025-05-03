-- 阶梯式负载生成器
stages = {
    {duration=5, rate=100},
    {duration=7, rate=200},
    {duration=10, rate=300}
}

function init(args)
    current_stage = 1
    start_time = os.time()
end

function request()
    return wrk.request()
end

function delay()
    local now = os.time() - start_time
    if current_stage <= #stages and now > stages[current_stage].duration then
        current_stage = current_stage + 1
    end

    local target_rate = stages[current_stage].rate
    return 1000 / target_rate  -- 转换为间隔毫秒
end