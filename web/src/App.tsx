import * as React from "react"
import {
  ActivityIcon,
  Clock3Icon,
  DatabaseIcon,
  DropletsIcon,
  MoonIcon,
  RefreshCwIcon,
  SunIcon,
  ThermometerIcon,
  WifiIcon,
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
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

const RANGE_OPTIONS = [3, 6, 12, 24] as const
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
    return { label: "数据延迟", variant: "warning" as const }
  }

  return { label: "实时更新", variant: "success" as const }
}

function getTrend(readings: Reading[]) {
  if (readings.length < 2) {
    return "暂无趋势"
  }

  const change = readings.at(-1)!.glucose_mmol - readings.at(-2)!.glucose_mmol
  if (Math.abs(change) < 0.05) {
    return "较上次持平"
  }

  return `较上次${change > 0 ? "上升" : "下降"} ${Math.abs(change).toFixed(1)}`
}

export function App() {
  const { setTheme } = useTheme()
  const [rangeHours, setRangeHours] = React.useState(24)
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

    const since = refreshedAt.getTime() - rangeHours * 60 * 60_000
    return readings.filter((reading) => {
      const timestamp = new Date(reading.timestamp).getTime()
      return Number.isFinite(timestamp) && timestamp >= since
    })
  }, [rangeHours, readings, refreshedAt])

  const latest = readings.at(-1) ?? null
  const connection = getConnectionState(latest, refreshedAt)
  const trend = getTrend(readings)

  const toggleTheme = () => {
    const isDark = document.documentElement.classList.contains("dark")
    setTheme(isDark ? "light" : "dark")
  }

  return (
    <div className="min-h-svh bg-background">
      <header className="border-b bg-background/95">
        <div className="mx-auto flex min-h-16 max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <DropletsIcon className="size-5" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold">OpenAnytime</h1>
              <p className="truncate text-sm text-muted-foreground">
                鱼跃 5 HSE · 本地连续血糖监测
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Badge variant={connection.variant}>
              <ActivityIcon data-icon="inline-start" aria-hidden="true" />
              {connection.label}
            </Badge>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon"
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

      <main className="mx-auto flex max-w-7xl flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <section
          className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center"
          aria-label="数据时间范围"
        >
          <ToggleGroup
            value={[String(rangeHours)]}
            onValueChange={(values) => {
              const nextValue = Number(values[0])
              if (
                RANGE_OPTIONS.includes(
                  nextValue as (typeof RANGE_OPTIONS)[number]
                )
              ) {
                setRangeHours(nextValue)
              }
            }}
            variant="outline"
            size="sm"
            spacing={0}
            aria-label="选择图表时间范围"
          >
            {RANGE_OPTIONS.map((hours) => (
              <ToggleGroupItem key={hours} value={String(hours)}>
                {hours} 小时
              </ToggleGroupItem>
            ))}
          </ToggleGroup>

          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Clock3Icon className="size-4" aria-hidden="true" />
            <span className="tabular-nums">
              更新于 {formatRefreshTime(refreshedAt)}
            </span>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="outline"
                    size="icon-sm"
                    aria-label="立即刷新数据"
                    disabled={isRefreshing}
                    onClick={() => void load()}
                  />
                }
              >
                <RefreshCwIcon
                  className={isRefreshing ? "animate-spin" : undefined}
                  data-icon="inline-start"
                  aria-hidden="true"
                />
              </TooltipTrigger>
              <TooltipContent>立即刷新数据</TooltipContent>
            </Tooltip>
          </div>
        </section>

        {error ? (
          <Alert variant="destructive">
            <DatabaseIcon aria-hidden="true" />
            <AlertTitle>无法读取本地数据库</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        <section
          className="grid grid-cols-2 gap-3 lg:grid-cols-4"
          aria-label="最新读数"
        >
          <MetricCard
            icon={DropletsIcon}
            label="当前血糖"
            value={latest?.glucose_mmol.toFixed(1) ?? "--"}
            unit="mmol/L"
            description={trend}
            loading={isInitialLoading}
          />
          <MetricCard
            icon={ActivityIcon}
            label="血糖换算"
            value={latest?.glucose_mg.toString() ?? "--"}
            unit="mg/dL"
            description="与当前读数同步"
            loading={isInitialLoading}
          />
          <MetricCard
            icon={ThermometerIcon}
            label="传感器温度"
            value={latest?.temperature_c.toFixed(1) ?? "--"}
            unit="°C"
            description="来自广播记录"
            loading={isInitialLoading}
          />
          <MetricCard
            icon={WifiIcon}
            label="BLE 信号"
            value={latest?.rssi?.toString() ?? "--"}
            unit="dBm"
            description="数值越接近 0 越强"
            loading={isInitialLoading}
          />
        </section>

        <React.Suspense fallback={<ChartFallback />}>
          <GlucoseChart
            readings={visibleReadings}
            rangeHours={rangeHours}
            loading={isInitialLoading}
          />
        </React.Suspense>
        <ReadingsTable readings={visibleReadings} loading={isInitialLoading} />

        <Separator />
        <footer className="flex flex-col justify-between gap-1 pb-2 text-xs text-muted-foreground sm:flex-row">
          <span>逆向研究数据，不用于医疗诊断。</span>
          <span>
            {visibleReadings.length} 条记录 · 最近 {rangeHours} 小时
          </span>
        </footer>
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
        <Skeleton className="h-80 w-full sm:h-96" />
      </CardContent>
    </Card>
  )
}

export default App
