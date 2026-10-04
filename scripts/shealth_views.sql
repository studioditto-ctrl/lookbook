-- Analysis views. Raw tables hold one row per device per day; Samsung's "Combined" device
-- (device_profile.name='Combined') is the de-duplicated source of truth.
DROP VIEW IF EXISTS v_daily_steps;
CREATE VIEW v_daily_steps AS
SELECT date(day_time) AS day, MAX(step_count) AS steps, MAX(walk_step_count) AS walk_steps,
       MAX(run_step_count) AS run_steps, MAX(distance) AS distance_m, MAX(calorie) AS kcal,
       MAX(active_time) / 60000.0 AS active_min
FROM tracker_pedometer_day_summary
WHERE deviceuuid = (SELECT deviceuuid FROM device_profile WHERE name = 'Combined')
GROUP BY date(day_time);

DROP VIEW IF EXISTS v_daily_calories;
CREATE VIEW v_daily_calories AS
SELECT date(day_time) AS day, SUM(rest_calorie) AS rest_kcal, SUM(active_calorie) AS active_kcal,
       SUM(tef_calorie) AS tef_kcal, SUM(rest_calorie + active_calorie + tef_calorie) AS total_kcal
FROM calories_burned_details GROUP BY date(day_time);

DROP VIEW IF EXISTS v_exercise;
CREATE VIEW v_exercise AS
SELECT datauuid, datetime(start_time, substr(time_offset, 4, 3) || ' hours') AS start_local,
       date(start_time, substr(time_offset, 4, 3) || ' hours') AS day,
       exercise_type,
       CASE exercise_type WHEN 1001 THEN 'walking' WHEN 1002 THEN 'running' WHEN 11007 THEN 'cycling'
            WHEN 13001 THEN 'hiking' WHEN 0 THEN 'custom' ELSE 'type_' || exercise_type END AS type_name,
       duration / 60000.0 AS duration_min, distance AS distance_m, calorie AS kcal,
       mean_speed, max_speed, mean_heart_rate, max_heart_rate, vo2_max, pkg_name, deviceuuid
FROM exercise;

DROP VIEW IF EXISTS v_weight;
CREATE VIEW v_weight AS
SELECT datetime(start_time, substr(time_offset, 4, 3) || ' hours') AS measured_local,
       weight, height, body_fat, body_fat_mass, skeletal_muscle_mass, basal_metabolic_rate, total_body_water, pkg_name
FROM weight;

-- stage: 40001 awake, 40002 light, 40003 deep, 40004 REM
DROP VIEW IF EXISTS v_sleep_session;
CREATE VIEW v_sleep_session AS
SELECT sleep_id, MIN(start_time) AS start_time, MAX(end_time) AS end_time,
       ROUND(SUM((julianday(end_time) - julianday(start_time)) * 1440), 1) AS total_min,
       ROUND(SUM(CASE stage WHEN 40001 THEN (julianday(end_time) - julianday(start_time)) * 1440 END), 1) AS awake_min,
       ROUND(SUM(CASE stage WHEN 40002 THEN (julianday(end_time) - julianday(start_time)) * 1440 END), 1) AS light_min,
       ROUND(SUM(CASE stage WHEN 40003 THEN (julianday(end_time) - julianday(start_time)) * 1440 END), 1) AS deep_min,
       ROUND(SUM(CASE stage WHEN 40004 THEN (julianday(end_time) - julianday(start_time)) * 1440 END), 1) AS rem_min
FROM sleep_stage GROUP BY sleep_id;

DROP VIEW IF EXISTS v_daily_summary;
CREATE VIEW v_daily_summary AS
SELECT s.day, s.steps, s.distance_m, s.active_min, c.total_kcal, c.active_kcal,
       (SELECT COUNT(*) FROM v_exercise e WHERE e.day = s.day) AS workouts,
       (SELECT ROUND(SUM(distance_m) / 1000, 2) FROM v_exercise e WHERE e.day = s.day AND e.type_name = 'running') AS run_km
FROM v_daily_steps s LEFT JOIN v_daily_calories c USING (day);
