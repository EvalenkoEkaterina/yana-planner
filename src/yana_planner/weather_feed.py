"""Graph-neutral weather values and fail-closed scenario restrictions."""
from datetime import datetime, timedelta, timezone
from math import isfinite

from .domain import InputError


def instant(value):
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise InputError('Нужно время с часовым поясом') from exc


def normalize(records):
    """Normalize uncorrected ECMWF wind; materialize null values explicitly."""
    output = []
    seen = set()
    for record in records:
        point = record['point']
        lat, lon = point['latitude'], point['longitude']
        if not point['id'] or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise InputError('Некорректная погодная точка')
        if record['source_model'] != 'ecmwf_ifs025' or record['unit'] != 'm/s':
            raise InputError('Ожидается ECMWF IFS 0.25, ветер в м/с')
        instant(record['retrieved_at'])
        issued = record.get('forecast_issued_at')
        if issued is not None:
            instant(issued)
        for hour in record['hourly']:
            start = instant(hour['time'])
            if start.minute or start.second or start.microsecond:
                raise InputError('Ожидаются целые часы')
            key = (point['id'], start)
            if key in seen:
                raise InputError('Повтор точки и часа')
            seen.add(key)
            value = hour.get('wind_speed_10m')
            if value is not None and (isinstance(value, bool) or
                    not isinstance(value, (int, float)) or not isfinite(value) or value < 0):
                raise InputError('Некорректный ветер')
            output.append(dict(point_id=point['id'], latitude=lat, longitude=lon,
                valid_from=start.isoformat(), valid_to=(start+timedelta(hours=1)).isoformat(),
                metric='wind_speed_10m', value=value, unit='m/s', data_kind='forecast',
                source=record['source'], source_model=record['source_model'],
                retrieved_at=record['retrieved_at'], forecast_issued_at=issued,
                correction_applied=False, missing=value is None,
                uncertainty=None, availability_at_decision_time='unverified',
                interval_basis='hourly integration assumption, [start,end)'))
    return output


def restrictions(values, point_ids, start, end, rule, *, mode='historical_demo', decision_at=None):
    """Require every requested point/hour before forming any restriction intervals."""
    begin, finish = instant(start), instant(end)
    if not point_ids or len(set(point_ids)) != len(point_ids):
        raise InputError('Нужен уникальный непустой список точек')
    seconds = (finish-begin).total_seconds()
    if seconds <= 0 or seconds % 3600 or begin.minute or begin.second or begin.microsecond:
        raise InputError('Нужен непустой период по целым часам')
    if rule.get('status') != 'scenario_assumption' or rule.get('metric') != 'wind_speed_10m' or rule.get('unit') != 'm/s' or rule.get('operator') != '>=':
        raise InputError('Нужно отдельное сценарное правило ветра')
    threshold = rule.get('threshold')
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not isfinite(threshold) or threshold <= 0:
        raise InputError('Некорректный порог')
    if mode not in ('historical_demo', 'as_of_decision'):
        raise InputError('Неизвестный режим')
    decision = instant(decision_at) if mode == 'as_of_decision' else None
    index = {}
    for row in values:
        if row['point_id'] not in point_ids or row['metric'] != 'wind_speed_10m':
            continue
        at = instant(row['valid_from'])
        if not begin <= at < finish:
            continue
        key = (row['point_id'], at)
        if key in index:
            raise InputError('Повтор точки и часа')
        if row['unit'] != 'm/s' or instant(row['valid_to']) != at+timedelta(hours=1):
            raise InputError('Несовместимые единицы или интервал')
        if row['correction_applied'] or row['data_kind'] != 'forecast' or row['source_model'] != 'ecmwf_ifs025':
            raise InputError('Для ограничений нужен исходный ECMWF без коррекции')
        index[key] = row
    gaps, maxima = [], []
    for h in range(int(seconds//3600)):
        at = begin+timedelta(hours=h)
        winds = []
        for point in point_ids:
            row = index.get((point, at))
            value = row.get('value') if row else None
            reason = None
            if row is None or row.get('missing') or value is None:
                reason = 'missing_value'
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
                reason = 'invalid_value'
            elif decision is not None and (row.get('forecast_issued_at') is None or
                    instant(row['forecast_issued_at']) > decision or instant(row['retrieved_at']) > decision):
                reason = 'availability_at_decision_unconfirmed'
            if reason:
                gaps.append(dict(point_id=point, valid_from=at.isoformat(), reason=reason))
            else:
                winds.append(value)
        maxima.append(max(winds) if len(winds)==len(point_ids) else None)
    common = dict(rule=dict(rule), point_ids=point_ids, t0=begin.isoformat(),
                  mode=mode, scope='entire_route_simplification',
                  limitation='Maximum of selected point cells; not full river coverage or vessel passage timing')
    if gaps:
        return dict(**common, status='insufficient_weather_data',
                    message='Недостаточно погодных данных', closures=None, gaps=gaps)
    closures = []
    for h, value in enumerate(maxima):
        if value >= threshold:
            if closures and closures[-1][1] == h:
                closures[-1][1] = h+1
            else:
                closures.append([h,h+1])
    return dict(**common, status='ok', closures=closures, gaps=[],
                hourly_max_wind_ms=maxima,
                availability_at_decision_time='unverified' if decision is None else 'confirmed_by_supplied_metadata')


def mapped_points(mapping, segment_ids):
    """Use only mappings explicitly confirmed against the integration team's graph."""
    if not segment_ids:
        raise InputError('Нужны выбранные участки')
    result = []
    for segment_id in segment_ids:
        entry = mapping.get('segments', {}).get(segment_id)
        if not entry or entry.get('status') != 'confirmed' or not entry.get('point_ids') or not entry.get('method') or not entry.get('limitations'):
            raise InputError('Нет согласованной погодной привязки участка')
        for point in entry['point_ids']:
            if point not in result:
                result.append(point)
    return result
