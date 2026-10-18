-- Verify Module:PJ:VG/HOT with Lua 5.1 and minimal Scribunto mocks.
-- Run from the repository root: lua tests/wiki/test_pj_vg_hot.lua [module-path]

local function equal(actual, expected, context)
    if actual ~= expected then
        error((context or "Unexpected output") .. "\nExpected: "
            .. tostring(expected) .. "\nActual: " .. tostring(actual), 2)
    end
end

local function contains(text, expected, context)
    if not text:find(expected, 1, true) then
        error((context or "Missing output") .. ": " .. expected
            .. "\nActual: " .. text, 2)
    end
end

local function excludes(text, unexpected, context)
    if text:find(unexpected, 1, true) then
        error((context or "Unexpected output") .. ": " .. unexpected
            .. "\nActual: " .. text, 2)
    end
end

local function format_number(number)
    equal(type(number), "number", "formatNum requires a number")
    local text = tostring(number)
    while true do
        local grouped, count = text:gsub("^(%-?%d+)(%d%d%d)", "%1,%2")
        text = grouped
        if count == 0 then
            return text
        end
    end
end

local function encode(value)
    return (value:gsub("[^%w%-_%.~]", function(character)
        return string.format("%%%02X", string.byte(character))
    end))
end

local function escape_attribute(value)
    return (value:gsub("&", "&amp;"):gsub('"', "&quot;")
        :gsub("<", "&lt;"):gsub(">", "&gt;"))
end

package.preload["strict"] = function()
    return true
end

local argument_calls = 0
package.preload["Module:Arguments"] = function()
    return {
        getArgs = function(frame)
            argument_calls = argument_calls + 1
            equal(type(frame.args), "table", "Frame arguments")
            return frame.args
        end,
    }
end

package.preload["Module:Class/icon"] = function()
    return {
        _main = function(args)
            return "CLASS(" .. (args[1] or "") .. ")"
        end,
    }
end

package.preload["Module:Importance/icon2"] = function()
    return {
        _main = function(args)
            return "IMPORTANCE(" .. (args[1] or "") .. ")"
        end,
    }
end

mw = {
    ustring = {format = string.format},
    text = {
        split = function(text, separator, plain)
            equal(separator, "-", "ISO date separator")
            equal(plain, true, "Literal date splitting")
            local parts = {}
            for part in text:gmatch("[^-]+") do
                parts[#parts + 1] = part
            end
            return parts
        end,
    },
    language = {
        new = function(code)
            equal(code, "zh", "Only number formatting needs a language")
            return {formatNum = function(_, number)
                return format_number(number)
            end}
        end,
    },
    html = {
        create = function(tag)
            equal(tag, "span", "HTML element")
            local span = {text = "", attributes = {}}
            function span:wikitext(text)
                self.text = text
                return self
            end
            function span:attr(name, value)
                assert(name == "class" or name == "title", "Unexpected attribute")
                self.attributes[#self.attributes + 1] = name .. '="'
                    .. escape_attribute(value) .. '"'
                return self
            end
            return setmetatable(span, {__tostring = function(value)
                return "<span " .. table.concat(value.attributes, " ")
                    .. ">" .. value.text .. "</span>"
            end})
        end,
    },
    uri = {
        new = function(base)
            equal(base, "//pageviews.wmcloud.org/", "Pageviews host")
            local uri = {query = {}}
            function uri:extend(query)
                equal(query.project, "zh.wikipedia.org", "Pageviews project")
                equal(query.platform, "all-access", "Pageviews platform")
                equal(query.agent, "user", "Pageviews agent")
                equal(query.redirects, "0", "Pageviews redirect handling")
                self.query = query
            end
            return setmetatable(uri, {__tostring = function(value)
                local keys, query = {}, {}
                for key in pairs(value.query) do
                    keys[#keys + 1] = key
                end
                table.sort(keys)
                for _, key in ipairs(keys) do
                    query[#query + 1] = encode(key) .. "="
                        .. encode(value.query[key])
                end
                return base .. "?" .. table.concat(query, "&")
            end})
        end,
    },
}

local module = dofile(arg[1] or "wiki/zhwiki/PJ_VG_HOT.lua")
equal(type(module._item), "function", "Direct item entry point")
equal(type(module.item), "function", "Frame item entry point")

local function arguments(overrides, omitted)
    local args = {
        [1] = "測試遊戲",
        [2] = "FA",
        [3] = "High",
        views = "12346",
        start = "2026-10-01",
        ["end"] = "2026-10-03",
        rank = "55",
        old_rank = "60",
    }
    for key, value in pairs(overrides or {}) do
        args[key] = value
    end
    if omitted then
        args[omitted] = nil
    end
    return args
end

local rendered = 0
local function render(overrides, omitted)
    rendered = rendered + 1
    return module._item(arguments(overrides, omitted))
end

local header = '<span class="icon_class">CLASS(FA)</span>'
    .. '<span class="icon_importance">IMPORTANCE(High)</span>[[測試遊戲]]'
    .. '<span class="separator">&emsp14;—&emsp14;</span>'
local link = '[//pageviews.wmcloud.org/?agent=user&end=2026-10-03'
    .. '&pages=%E6%B8%AC%E8%A9%A6%E9%81%8A%E6%88%B2&platform=all-access'
    .. '&project=zh.wikipedia.org&redirects=0&start=2026-10-01 12,346]'
equal(render({top = "daily/59"}), header .. link
    .. '<span class="average_views average_views_top"'
    .. ' title="全站日间第59名">（4,115）</span>'
    .. '<span class="trend trend_rise"'
    .. ' title="上期第60名，本期第55名">▲5</span>',
    "Complete entry preserves icons, link, floor average, and trend")

local valid = {
    {"daily/59", "日间", 59},
    {"monthly/125", "月间", 125},
}
for _, case in ipairs(valid) do
    local output = render({top = case[1]})
    contains(output, 'class="average_views average_views_top" title="全站'
        .. case[2] .. '第' .. case[3] .. '名">（4,115）</span>',
        "Typed rank: " .. case[1])
end

local unranked = render()
contains(unranked, '<span class="average_views">（4,115）</span>')
excludes(unranked, "全站", "Absent top does not invent a site rank")

local daily_month = render({top = "daily/59", ["end"] = "2026-10-31"})
contains(daily_month, 'title="全站日间第59名">（398）</span>',
    "Daily period comes from top despite a complete calendar month")
local monthly_day = render({top = "monthly/125", ["end"] = "2026-10-01"})
contains(monthly_day, 'title="全站月间第125名">（12,346）</span>',
    "Monthly period comes from top despite a single-day interval")
local yearly = render({top = "daily/59", start = "2026-01-01",
    ["end"] = "2026-12-31"})
contains(yearly, 'title="全站日间第59名">（33）</span>',
    "A calendar year does not override the typed period")
contains(render({views = "10"}), 'class="average_views">（3）</span>',
    "Daily averages floor fractional results over inclusive days")
contains(render({views = "0"}), 'class="average_views">（0）</span>',
    "Measured zero remains a valid view count")

local rises = {
    {49, "", "49"},
    {50, " trend_rise_50", "50"},
    {99, " trend_rise_50", "99"},
    {100, " trend_rise_50 trend_rise_100", "100"},
    {199, " trend_rise_50 trend_rise_100", "199"},
    {200, " trend_rise_50 trend_rise_100 trend_rise_200", "200"},
    {1000, " trend_rise_50 trend_rise_100 trend_rise_200", "1,000"},
    {1001, " trend_rise_50 trend_rise_100 trend_rise_200", "999+"},
}
for _, case in ipairs(rises) do
    local old = case[1] + 1
    local output = render({rank = "1", old_rank = tostring(old)})
    contains(output, '<span class="trend trend_rise' .. case[2]
        .. '" title="上期第' .. format_number(old) .. '名，本期第1名">▲'
        .. case[3] .. '</span>', "Rise threshold: " .. case[1])
end
contains(render({rank = "1002", old_rank = "1"}),
    '<span class="trend trend_fall"'
    .. ' title="上期第1名，本期第1,002名">▼1,001</span>',
    "Falling ranks keep their uncapped movement")
contains(render({old_rank = "55"}), '<span class="trend trend_unchanged"'
    .. ' title="上期第55名，本期第55名">—0</span>', "Unchanged rank")
contains(render({}, "old_rank"), '<span class="trend trend_new"'
    .. ' title="上期无记录，本期第55名">' .. "'''New'''</span>", "New rank")

equal(render({top = "daily/59"}, "views"), header .. "資料缺失",
    "Missing views preserve missing-data output")
equal(render({top = "monthly/125"}, "rank"), header .. "資料缺失",
    "Missing rank preserves missing-data output")

local fixture_tops = {
    [2] = {"daily/59", "日间", 59},
    [27] = {"monthly/125", "月间", 125},
}
for index = 1, 50 do
    local top = fixture_tops[index]
    local daily = index <= 25
    local output = render({
        [1] = "遊戲 " .. index,
        [2] = index % 2 == 0 and "GA" or "Start",
        [3] = index % 3 == 0 and "Mid" or "High",
        views = tostring(50000 - index * 73),
        start = daily and "2026-10-17" or "2026-09-01",
        ["end"] = daily and "2026-10-17" or "2026-09-30",
        rank = tostring(index),
        old_rank = tostring(index + 3),
        top = top and top[1] or nil,
    })
    equal(output:sub(1, 1), "<", "Fixture rows are inline without a leading #")
    contains(output, "[[遊戲 " .. index .. "]]", "Fixture article title")
    if top then
        contains(output, 'class="average_views average_views_top" title="全站'
            .. top[2] .. '第' .. top[3] .. '名">', "Fixture typed site rank")
    else
        contains(output, '<span class="average_views">',
            "Unranked fixture row retains its average")
        excludes(output, "average_views_top", "Unranked row has no rank class")
        excludes(output, "全站", "Unranked row has no site-rank tooltip")
    end
end

local bare = arguments()
bare[2], bare[3] = nil, nil
contains(module._item(bare), '<span class="icon_class">CLASS()</span>'
    .. '<span class="icon_importance">IMPORTANCE()</span>[[測試遊戲]]',
    "Missing assessments still use the existing icon builders")
local wrapped = arguments({top = "monthly/125"})
equal(module.item({args = wrapped}), module._item(wrapped), "Frame wrapper")
equal(argument_calls, 1, "The frame wrapper uses Module:Arguments")
equal(wrapped.top, "monthly/125", "The raw typed top argument remains unchanged")

print("PJ_VG_HOT: " .. rendered .. " rendered cases passed")
