# Business Context & Query Rules

## Operators
- The three operators in Saudi Arabia are: STC, Mobily, Zain
- "Competitors" or "other operators" relative to STC means: Mobily and Zain

## Geography
- "Kingdom", "Kingdom-wide", or "national" → filter: place_type = 'country' AND place_name = 'Saudi Arabia'
- "City" or "locality" level → filter: place_type = 'locality'
- "Region" or "province" level → filter: place_type = 'administrative_area_level_1'

## Time Periods
- aggregation_period values are EXACTLY (case-sensitive): 'Day', 'Week', 'MTD', 'Month'
- "Latest" or "most recent" → ORDER BY start_date DESC LIMIT 1
- "Past 4 weeks" → aggregation_period = 'Week' ORDER BY start_date DESC LIMIT 4
- "This month" or "monthly" → aggregation_period = 'Month'
- "Year to date" or "overall" → aggregation_period = 'MTD'

## Technology
- "5G speeds" → technology_type = '5G'
- "4G speeds" or "LTE" → technology_type = '4G'
- Always specify technology_type in WHERE clause unless question covers all technologies

## Investment & Coverage Gaps
- "Where should STC invest?" or "investment focus areas" or "coverage gaps" →
  Find localities where STC's median_download_mbps is lower than Mobily OR Zain
  for the same place_name. Use CASE WHEN pivot grouped by place_name with HAVING clause.
- SQL pattern for investment analysis:
  SELECT place_name,
    ROUND(AVG(CASE WHEN operator = 'STC' THEN median_download_mbps END), 2) AS stc_download,
    ROUND(AVG(CASE WHEN operator = 'Zain' THEN median_download_mbps END), 2) AS zain_download,
    ROUND(AVG(CASE WHEN operator = 'Mobily' THEN median_download_mbps END), 2) AS mobily_download
  FROM ookla_aggregated_data
  WHERE technology_type = '5G' AND place_type = 'locality' AND aggregation_period = 'Month'
  GROUP BY place_name
  HAVING stc_download < zain_download OR stc_download < mobily_download
  ORDER BY stc_download ASC

## Speed Metrics
- "Download speed" → median_download_mbps (preferred over mean)
- "Upload speed" → median_upload_mbps
- "Latency" → median_latency_ms
- Always use ROUND(..., 2) on speed values

## General Rules
- Never use aggregation_period values other than the four listed above
- For kingdom-wide queries always include place_type and place_name filters
- NaN/NULL values appear in pivot queries when an operator has no data for that area — this is expected
