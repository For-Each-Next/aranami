--- Supplement Module for [[PJ:VG/HOT]].
--- Site-wide top ranks use `top=daily/59` or `top=monthly/125`.

require("strict")

local strf = mw.ustring.format

local getArgs = require("Module:Arguments").getArgs
local get_cls_icon = require("Module:Class/icon")._main
local get_imp_icon = require("Module:Importance/icon2")._main

--- @alias PageTitle string @A wiki page title.
--- @alias IsoDate string @A date string formatted as `YYYY-MM-DD`.
--- @alias UnixTime number @A Unix timestamp (seconds since epoch).
--- @alias Rank number @A ranking position.
--- @alias SiteTop string @A daily/monthly period and rank separated by a slash.
--- @alias Wikitext string @Wikitext output, which may contain HTML tags.
--- @alias Arguments table<string | number, string?> @An argument table.

local SOAR_THRESHOLDS = {50, 100, 200}
local SITE_TOP_PERIODS = {daily = "日间", monthly = "月间"}


-- ==== Utilities ==== --

--- Parse a date string into a Unix timestamp,
--- assuming a time of `00:00:00`.
--- Example: "2010-01-01" returns 1262304000.
--- @param date IsoDate @The date string to parse.
--- @return UnixTime @The corresponding Unix timestamp.
local function strpdate(date)
    local strings = mw.text.split(date, "-", true)
    local t = {
        year = tonumber(strings[1]),
        month = tonumber(strings[2]),
        day = tonumber(strings[3]),
        hour = 0,
        min = 0,
        sec = 0,
    }
    return os.time(t)
end


--- Compute the inclusive number of days between two dates.
--- Example: "2005-01-01" to "2005-01-01" returns 1.
--- @param from IsoDate @The start date.
--- @param to IsoDate @The end date.
--- @return number @The number of days in the range (inclusive).
local function day_diff(from, to)
    local t1 = strpdate(from)
    local t2 = strpdate(to)
    local result = os.difftime(t2, t1) / 86400 + 1
    return result
end


--- Compute the average daily value, rounded down.
--- @param total number @The total count.
--- @param from IsoDate @The start date.
--- @param to IsoDate @The end date.
--- @return number @The daily average (floored).
local function daily_average(total, from, to)
    local days = day_diff(from, to)
    return math.floor(total / days)
end


--- Format a number using thousand separators.
--- @param num number @The number to format.
--- @return string @The formatted number string.
local function strfnum(num)
    return mw.language.new("zh"):formatNum(num)
end


--- Construct an HTML `<span>` element.
--- @param text Wikitext @The inner wikitext.
--- @param class string @The CSS class name(s).
--- @param title string? @The optional tooltip text.
--- @return Wikitext @The rendered HTML span markup.
local function span_builder(text, class, title)
    local span = mw.html.create("span"):wikitext(text)
    if class then
        span:attr("class", class)
    end
    if title then
        span:attr("title", title)
    end
    return tostring(span)
end


--- Build a Pageviews URL for a given title and date range.
--- @param title PageTitle @The page title.
--- @param from IsoDate @The start date.
--- @param to IsoDate @The end date.
--- @return string @The encoded URL.
local function pageviews_url(title, from, to)
    local uri = mw.uri.new("//pageviews.wmcloud.org/")
    local query = {
        ["project"] = "zh.wikipedia.org",
        ["platform"] = "all-access",
        ["agent"] = "user",
        ["redirects"] = "0",
        ["pages"] = title,
        ["start"] = from,
        ["end"] = to,
    }
    uri:extend(query)
    return tostring(uri)
end


--- Read a typed daily or monthly official site-wide rank.
--- @param top SiteTop? @The optional daily/rank or monthly/rank argument.
--- @return string?, Rank? @The tooltip period and site-wide rank.
local function parse_site_top(top)
    if not top then
        return nil
    end

    local period, rank = top:match("^([a-z]+)/(%d+)$")
    return SITE_TOP_PERIODS[period], tonumber(rank)
end


--- Generate a description of old and new ranks.
--- @param old Rank? @The previous rank.
--- @param new Rank @The current rank.
--- @return string @A textual rank-change description.
local function rank_change_desc(old, new)
    local old_desc = old and strf("上期第%s名", strfnum(old)) or "上期无记录"
    local new_desc = strf("本期第%s名", strfnum(new))
    return strf("%s，%s", old_desc, new_desc)
end


--- Preprocess rank-change data for presentation.
--- @param old Rank? @The previous rank.
--- @param new Rank? @The current rank.
--- @return table @A tuple-like table: `{status, places}`.
local function rank_change_preprocess(old, new)
    if old == nil then
        return {"new"}
    end

    local delta = new - old
    local status

    if delta < 0 then
        status = "rise"
    elseif delta > 0 then
        status = "fall"
    else
        status = "unchanged"
    end

    local places = math.abs(delta)
    return {status, places}
end


--- Transform rank-change data into display metadata.
--- @param status "new" | "rise" | "fall" | "unchanged" @The rank change status.
--- @param places number @The number of positions changed.
--- @return table @A tuple-like table: `{class_list, mark_text}`.
local function rank_change_process(status, places)
    if status == "new" then
        local mark = "'''New'''"
        local classes = {"new"}
        return {classes, mark}
    end

    if status == "unchanged" then
        local mark = strf("%s%s", "—", strfnum(places))
        local classes = {"unchanged"}
        return {classes, mark}
    end

    if status == "fall" then
        local mark = strf("%s%s", "▼", strfnum(places))
        local classes = {"fall"}
        return {classes, mark}
    end

    -- status == "rise"
    local text = (places > 1000) and (strfnum(999) .. "+") or strfnum(places)
    local mark = strf("%s%s", "▲", text)
    local classes = {"rise"}

    for _, threshold in ipairs(SOAR_THRESHOLDS) do
        if places >= threshold then
            table.insert(classes, strf("rise_%d", threshold))
        end
    end

    return {classes, mark}
end


-- ==== Item builders ==== --

--- Build a class icon for an item.
--- @param grade string? @The class grade.
--- @return Wikitext @The rendered class icon.
local function build_item_class_icon(grade)
    local icon = get_cls_icon({grade})
    return span_builder(icon, "icon_class")
end


--- Build an importance icon for an item.
--- @param grade string? @The importance grade.
--- @return Wikitext @The rendered importance icon.
local function build_item_importance_icon(grade)
    local icon = get_imp_icon({grade})
    return span_builder(icon, "icon_importance")
end


--- Build a wikilink for an article.
--- @param title PageTitle @The page title.
--- @return Wikitext @The wikilink markup.
local function build_item_article_link(title)
    return strf("[[%s]]", title)
end


--- Build the item header information fragment.
--- @param title PageTitle @The page title.
--- @param cls string? @The class grade.
--- @param imp string? @The importance grade.
--- @return Wikitext @The combined header wikitext.
local function build_item_info(title, cls, imp)
    local result = {
        build_item_class_icon(cls),
        build_item_importance_icon(imp),
        build_item_article_link(title),
    }
    return table.concat(result)
end


--- Build a separator between item sections.
--- @return Wikitext @The separator.
local function build_item_separator()
    local text = "&emsp14;—&emsp14;"
    return span_builder(text, "separator")
end


--- Build the total-views with linking for an item.
--- @param title PageTitle @The page title.
--- @param views number @The total number of views.
--- @param from IsoDate @The start date.
--- @param to IsoDate @The end date.
--- @return Wikitext @The external link with formatted total views.
local function build_item_total_views(title, views, from, to)
    local views_url = pageviews_url(title, from, to)
    local views_count = strfnum(views)
    return strf("[%s %s]", views_url, views_count)
end


--- Build the average daily views with an optional site-rank tooltip.
--- @param views number @The total number of views.
--- @param from IsoDate @The inclusive start date.
--- @param to IsoDate @The inclusive end date.
--- @param top SiteTop? @The typed official daily or monthly site-wide rank.
--- @return Wikitext @The formatted average daily views.
local function build_item_average_views(views, from, to, top)
    local avg_views = daily_average(views, from, to)
    local text = strf("（%s）", strfnum(avg_views))

    local period, rank = parse_site_top(top)

    if period then
        local tooltip = strf("全站%s第%d名", period, rank)
        return span_builder(
            text,
            "average_views average_views_top",
            tooltip
        )
    end

    return span_builder(text, "average_views")
end


--- Build the rank-change indicator.
--- @param old Rank? @The previous rank.
--- @param new Rank @The current rank.
--- @return Wikitext @The rank-change mark.
local function build_item_rank_change(old, new)
    local desc = rank_change_desc(old, new)
    local status, places = unpack(rank_change_preprocess(old, new))
    local classes, mark = unpack(rank_change_process(status, places))
    local class_names = {"trend"}

    for _, c in ipairs(classes) do
        table.insert(class_names, strf("trend_%s", c))
    end

    local class_text = table.concat(class_names, " ")
    return span_builder(mark, class_text, desc)
end


--- Build the views information fragment for an item.
--- @param title PageTitle @The page title.
--- @param views number? @The total number of views.
--- @param from IsoDate @The inclusive start date.
--- @param to IsoDate @The inclusive end date.
--- @param rank Rank? @The current project rank.
--- @param old_rank Rank? @The previous project rank.
--- @param top SiteTop? @The typed official daily or monthly site-wide rank.
--- @return Wikitext @The views information.
local function build_item_views_info(title, views, from, to, rank, old_rank, top)
    if views == nil or rank == nil then
        return "資料缺失"
    end

    local result = {
        build_item_total_views(title, views, from, to),
        build_item_average_views(views, from, to, top),
        build_item_rank_change(old_rank, rank),
    }
    return table.concat(result)
end


--- Build an inline item entry for the caller's list markup.
--- @param data Arguments @The item data.
--- @return Wikitext @The rendered item entry.
local function item(data)
    local title = data[1]
    local cls = data[2]
    local imp = data[3]
    local views = tonumber(data["views"])
    local from = data["start"]
    local to = data["end"]
    local rank = tonumber(data["rank"])
    local rank_old = tonumber(data["old_rank"])
    local top = data["top"]

    local result = {
        build_item_info(title, cls, imp),
        build_item_separator(),
        build_item_views_info(title, views, from, to, rank, rank_old, top),
    }
    return table.concat(result)
end


-- ==== Main ==== --

local p = {}

--- Create a frame-based wrapper for an exported function.
--- @param funcName string @The exported function name.
--- @return function @The frame-based invocation function.
local function makeInvokeFunc(funcName)
    return function(frame)
        local args = getArgs(frame)
        return p[funcName](args)
    end
end


p.item = makeInvokeFunc("_item")

--- Entry point for rendering a single item.
--- @param args Arguments @Frame arguments passed from the template.
--- @return Wikitext @The rendered item wikitext.
function p._item(args)
    return item(args)
end

return p
