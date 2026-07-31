export type Reading = {
  id: number
  timestamp: string
  counter: number | null
  reading_index: number | null
  glucose_mmol: number
  glucose_mg: number
  temperature_c: number
  rssi: number | null
  raw_hex: string | null
}

export type Scan = {
  timestamp: string
  counter: number | null
  rssi: number | null
  record_count: number | null
}

type ReadingsResponse = {
  readings: Reading[]
  scans: Scan[]
  count: number
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value)
}

function isReading(value: unknown): value is Reading {
  if (!value || typeof value !== "object") {
    return false
  }

  const reading = value as Record<string, unknown>
  return (
    isFiniteNumber(reading.id) &&
    typeof reading.timestamp === "string" &&
    isNullableNumber(reading.counter) &&
    isNullableNumber(reading.reading_index) &&
    isFiniteNumber(reading.glucose_mmol) &&
    isFiniteNumber(reading.glucose_mg) &&
    isFiniteNumber(reading.temperature_c) &&
    isNullableNumber(reading.rssi) &&
    (reading.raw_hex === null || typeof reading.raw_hex === "string")
  )
}

function isScan(value: unknown): value is Scan {
  if (!value || typeof value !== "object") {
    return false
  }

  const scan = value as Record<string, unknown>
  return (
    typeof scan.timestamp === "string" &&
    isNullableNumber(scan.counter) &&
    isNullableNumber(scan.rssi) &&
    isNullableNumber(scan.record_count)
  )
}

export async function fetchReadings(
  signal?: AbortSignal
): Promise<ReadingsResponse> {
  const response = await fetch("/api/readings?limit=2000", {
    headers: { Accept: "application/json" },
    signal,
  })

  if (!response.ok) {
    throw new Error(`数据服务返回 ${response.status}`)
  }

  const value: unknown = await response.json()
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("数据服务返回了无法识别的格式")
  }

  const payload = value as Record<string, unknown>
  if (!Array.isArray(payload.readings)) {
    throw new Error("数据服务返回了无法识别的格式")
  }

  const readings = payload.readings.filter(isReading)
  if (readings.length !== payload.readings.length) {
    throw new Error("部分血糖记录字段不完整，已拒绝展示")
  }

  const scans = Array.isArray(payload.scans) ? payload.scans.filter(isScan) : []
  return { readings, scans, count: readings.length }
}
