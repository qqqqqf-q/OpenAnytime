import * as React from "react"
import {
  ActivityIcon,
  Clock3Icon,
  DatabaseIcon,
  DropletsIcon,
  MoonIcon,
  RefreshCwIcon,
  SunIcon,
} from "lucide-react"

import { MetricCard } from "@/components/dashboard/metric-card"
import { ReadingsTable } from "@/components/dashboard/readings-table"
import { useTheme } from "@/components/theme-provider"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { fetchReadings, type Reading } from "@/lib/api"

const GlucoseChart = React.lazy(() =>
  import("@/components/dashboard/glucose-chart").then((module) => ({
    default: module.GlucoseChart,
  }))
)

type TimeRange = { start: number; end: number }

const REFRESH_INTERVAL_MS = 60_000
const STALE_AFTER_MS = 10 * 60_000

function formatRefreshTime(value: Date | null) {
  if (!value) {
    return "尚未同步"
  }

  return value.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

function getConnectionState(latest: Reading | null, refreshedAt: Date | null) {
  if (!latest || !refreshedAt) {
    return { label: "等待数据", variant: "outline" as const }
  }

  const age = refreshedAt.getTime() - new Date(latest.timestamp).getTime()
  if (!Number.isFinite(age) || age > STALE_AFTER_MS) {
    return { label: "数据延迟", variant: "destructive" as const }
  }

  return { label: "实时更新", variant: "secondary" as const }
}

function getTrendDelta(readings: Reading[]) {
  if (readings.length < 2) {
    return "--"
  }

  const change = readings.at(-1)!.glucose_mmol - readings.at(-2)!.glucose_mmol
  if (Math.abs(change) < 0.05) {
    return "0.0"
  }

  return `${change > 0 ? "+" : ""}${change.toFixed(1)}`
}

export function App() {
  const { setTheme } = useTheme()
  const [rangeHours, setRangeHours] = React.useState(3)
  const [customRange, setCustomRange] = React.useState<TimeRange | null>(null)
  const [readings, setReadings] = React.useState<Reading[]>([])
  const [isInitialLoading, setIsInitialLoading] = React.useState(true)
  const [isRefreshing, setIsRefreshing] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [refreshedAt, setRefreshedAt] = React.useState<Date | null>(null)
  const requestInFlight = React.useRef(false)

  const load = React.useCallback(async (signal?: AbortSignal) => {
    if (requestInFlight.current) {
      return
    }

    requestInFlight.current = true
    setIsRefreshing(true)

    try {
      const result = await fetchReadings(signal)
      setReadings(result.readings)
      setRefreshedAt(new Date())
      setError(null)
    } catch (loadError) {
      if (
        loadError instanceof DOMException &&
        loadError.name === "AbortError"
      ) {
        return
      }

      setError(
        loadError instanceof Error
          ? loadError.message
          : "读取数据时发生未知错误"
      )
    } finally {
      requestInFlight.current = false
      setIsInitialLoading(false)
      setIsRefreshing(false)
    }
  }, [])

  React.useEffect(() => {
    const controller = new AbortController()
    const initialLoad = window.setTimeout(() => {
      void load(controller.signal)
    }, 0)

    const interval = window.setInterval(() => {
      void load()
    }, REFRESH_INTERVAL_MS)

    return () => {
      controller.abort()
      window.clearTimeout(initialLoad)
      window.clearInterval(interval)
    }
  }, [load])

  const visibleReadings = React.useMemo(() => {
    if (!refreshedAt) {
      return []
    }
    if (customRange) {
      return readings.filter((reading) => {
        const timestamp = new Date(reading.timestamp).getTime()
        return (
          Number.isFinite(timestamp) &&
          timestamp >= customRange.start &&
          timestamp <= customRange.end
        )
      })
    }
    if (rangeHours === 0) {
      return readings
    }

    const since = refreshedAt.getTime() - rangeHours * 60 * 60_000
    return readings.filter((reading) => {
      const timestamp = new Date(reading.timestamp).getTime()
      return Number.isFinite(timestamp) && timestamp >= since
    })
  }, [rangeHours, customRange, readings, refreshedAt])

  const latest = readings.at(-1) ?? null
  const connection = getConnectionState(latest, refreshedAt)
  const trendDelta = getTrendDelta(readings)

  const toggleTheme = () => {
    const isDark = document.documentElement.classList.contains("dark")
    setTheme(isDark ? "light" : "dark")
  }

  return (
    <div className="flex min-h-svh flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center border-b">
        <div className="mx-auto flex w-full max-w-[1600px] items-center gap-2 px-4 lg:px-6">
          <DropletsIcon className="size-4" aria-hidden="true" />
          <h1 className="text-base font-medium">OpenAnytime</h1>
          <Separator
            orientation="vertical"
            className="mx-1 hidden h-4 sm:block data-vertical:self-auto"
          />
          <span className="hidden text-sm text-muted-foreground sm:inline">
            鱼跃 5 HSE
          </span>

          <div className="ml-auto flex items-center gap-2">
            <Badge variant={connection.variant}>
              <ActivityIcon data-icon="inline-start" aria-hidden="true" />
              {connection.label}
            </Badge>
            <div className="hidden items-center gap-1.5 text-xs text-muted-foreground md:flex">
              <Clock3Icon className="size-3.5" aria-hidden="true" />
              <span className="tabular-nums">
                {formatRefreshTime(refreshedAt)}
              </span>
            </div>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="立即刷新数据"
                    disabled={isRefreshing}
                    onClick={() => void load()}
                  />
                }
              >
                {isRefreshing ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <RefreshCwIcon data-icon="inline-start" aria-hidden="true" />
                )}
              </TooltipTrigger>
              <TooltipContent>立即刷新数据</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="切换明暗主题"
                    onClick={toggleTheme}
                  />
                }
              >
                <SunIcon
                  className="hidden dark:block"
                  data-icon="inline-start"
                  aria-hidden="true"
                />
                <MoonIcon
                  className="dark:hidden"
                  data-icon="inline-start"
                  aria-hidden="true"
                />
              </TooltipTrigger>
              <TooltipContent>切换明暗主题</TooltipContent>
            </Tooltip>
          </div>
        </div>
      </header>

      <main className="@container/main flex flex-1 flex-col">
        <div className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
          {error ? (
            <div className="px-4 lg:px-6">
              <Alert variant="destructive">
                <DatabaseIcon aria-hidden="true" />
                <AlertTitle>无法读取本地数据库</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            </div>
          ) : null}

          <section
            className="grid grid-cols-1 gap-4 px-4 *:data-[slot=card]:bg-linear-to-t *:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card *:data-[slot=card]:shadow-xs lg:px-6 @5xl/main:grid-cols-3 dark:*:data-[slot=card]:bg-card"
            aria-label="监测概览"
          >
            <MetricCard
              label="当前血糖"
              value={latest?.glucose_mmol.toFixed(1) ?? "--"}
              unit="mmol/L"
              action={<Badge variant="outline">{trendDelta}</Badge>}
              loading={isInitialLoading}
            />
            <section
              className="order-2 @5xl/main:order-4 @5xl/main:col-span-3"
              aria-label="血糖趋势图"
            >
              <React.Suspense fallback={<ChartFallback />}>
                <GlucoseChart
                  readings={visibleReadings}
                  rangeHours={rangeHours}
                  customRange={customRange}
                  windowEnd={refreshedAt?.getTime() ?? null}
                  loading={isInitialLoading}
                  onRangeChange={(hours) => {
                    setRangeHours(hours)
                    setCustomRange(null)
                  }}
                  onCustomRangeChange={setCustomRange}
                />
              </React.Suspense>
            </section>
            <MetricCard
              className="order-3 @5xl/main:order-2"
              label="传感器温度"
              value={latest?.temperature_c.toFixed(1) ?? "--"}
              unit="°C"
              loading={isInitialLoading}
            />
            <MetricCard
              className="order-4 @5xl/main:order-3"
              label="BLE 信号"
              value={latest?.rssi?.toString() ?? "--"}
              unit="dBm"
              loading={isInitialLoading}
            />
          </section>

          <section className="px-4 lg:px-6" aria-label="最近读数表格">
            <ReadingsTable
              readings={visibleReadings}
              loading={isInitialLoading}
            />
          </section>

          <Separator className="mt-auto" />
          <footer className="flex flex-col justify-between gap-1 px-4 text-xs text-muted-foreground sm:flex-row lg:px-6">
            <span>逆向研究数据，不用于医疗诊断。</span>
            <span>
              {visibleReadings.length} 条记录 ·{" "}
              {customRange
                ? "自定义范围"
                : rangeHours === 0
                  ? "全部"
                  : `最近 ${rangeHours} 小时`}
            </span>
          </footer>
        </div>
      </main>
    </div>
  )
}

function ChartFallback() {
  return (
    <Card aria-hidden="true">
      <CardHeader>
        <CardTitle>血糖趋势</CardTitle>
        <CardDescription>正在准备图表</CardDescription>
      </CardHeader>
      <CardContent>
        <Skeleton className="h-[280px] w-full sm:h-[340px]" />
      </CardContent>
    </Card>
  )
}

export default App
