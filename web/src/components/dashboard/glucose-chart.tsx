import { useState } from "react"
import { CalendarIcon, ChartNoAxesCombinedIcon } from "lucide-react"
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { Reading } from "@/lib/api"

// 0 是「全部」的哨兵值:小时数无法表达"从头到现在",且不与时长选项冲突
const RANGE_OPTIONS = [3, 6, 12, 24, 0] as const
const TARGET_GLUCOSE_RANGE = { min: 3.9, max: 7.8 } as const

const chartConfig = {
  glucose_mmol: {
    label: "血糖 mmol/L",
    color: "var(--primary)",
  },
} satisfies ChartConfig

function formatAxisTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatTooltipTime(timestamp: number) {
  return new Date(timestamp).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

// datetime-local 输入框的值格式(本地时区,精确到分)
function toDateTimeLocalValue(timestamp: number) {
  const date = new Date(timestamp)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export type TimeRange = { start: number; end: number }

type GlucoseChartProps = {
  readings: Reading[]
  rangeHours: number
  customRange: TimeRange | null
  windowEnd: number | null
  loading: boolean
  onRangeChange: (rangeHours: number) => void
  onCustomRangeChange: (range: TimeRange | null) => void
}

function RangeToggle({
  rangeHours,
  customRange,
  onRangeChange,
  className,
}: {
  rangeHours: number
  customRange: TimeRange | null
  onRangeChange: (rangeHours: number) => void
  className?: string
}) {
  return (
    <ToggleGroup
      multiple={false}
      value={customRange ? [] : [String(rangeHours)]}
      onValueChange={(values) => {
        const nextValue = Number(values[0])
        if (
          RANGE_OPTIONS.includes(nextValue as (typeof RANGE_OPTIONS)[number])
        ) {
          onRangeChange(nextValue)
        }
      }}
      variant="outline"
      size="sm"
      spacing={0}
      aria-label="选择图表时间范围"
      className={className}
    >
      {RANGE_OPTIONS.map((hours) => (
        <ToggleGroupItem key={hours} value={String(hours)}>
          {hours === 0 ? "全部" : `${hours}h`}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

// 自定义起止时间选择。草稿态留在弹层内,只有「应用」才提交给上层,
// 避免半成品输入(只填了一端)驱动图表。
function CustomRangePicker({
  customRange,
  bounds,
  onApply,
}: {
  customRange: TimeRange | null
  bounds: TimeRange | null
  onApply: (range: TimeRange | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [startText, setStartText] = useState("")
  const [endText, setEndText] = useState("")

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      setStartText(toDateTimeLocalValue(customRange?.start ?? bounds?.start ?? 0))
      setEndText(toDateTimeLocalValue(customRange?.end ?? bounds?.end ?? 0))
    }
    setOpen(nextOpen)
  }

  const apply = () => {
    const start = new Date(startText).getTime()
    const end = new Date(endText).getTime()
    if (Number.isFinite(start) && Number.isFinite(end) && start < end) {
      onApply({ start, end })
      setOpen(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger
        render={<Button variant={customRange ? "secondary" : "outline"} size="sm" />}
      >
        <CalendarIcon data-icon="inline-start" aria-hidden="true" />
        自定义
      </PopoverTrigger>
      <PopoverContent align="end" className="w-auto">
        <div className="grid gap-3">
          <label className="grid gap-1 text-xs text-muted-foreground">
            从
            <Input
              type="datetime-local"
              value={startText}
              min={bounds ? toDateTimeLocalValue(bounds.start) : undefined}
              max={bounds ? toDateTimeLocalValue(bounds.end) : undefined}
              onChange={(event) => setStartText(event.target.value)}
              className="w-auto"
            />
          </label>
          <label className="grid gap-1 text-xs text-muted-foreground">
            到
            <Input
              type="datetime-local"
              value={endText}
              min={bounds ? toDateTimeLocalValue(bounds.start) : undefined}
              max={bounds ? toDateTimeLocalValue(bounds.end) : undefined}
              onChange={(event) => setEndText(event.target.value)}
              className="w-auto"
            />
          </label>
          <div className="flex justify-end gap-2">
            {customRange ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  onApply(null)
                  setOpen(false)
                }}
              >
                清除
              </Button>
            ) : null}
            <Button size="sm" onClick={apply}>
              应用
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}

export function GlucoseChart({
  readings,
  rangeHours,
  customRange,
  windowEnd,
  loading,
  onRangeChange,
  onCustomRangeChange,
}: GlucoseChartProps) {
  // A categorical timestamp axis stretches sparse samples across the card. A
  // numeric domain keeps the selected range tied to actual wall-clock time.
  const chartData = readings
    .map((reading) => ({
      ...reading,
      timestamp_ms: new Date(reading.timestamp).getTime(),
    }))
    .filter((reading) => Number.isFinite(reading.timestamp_ms))
  // 显示值是 0.1 mmol/L 量化的官方口径,直接连线是台阶状的硬切换。
  // 这里做一层 1-2-1 加权的轻度渲染平滑(只影响线条,不改变数据,
  // tooltip 仍显示真实值):跨多个点的真实峰谷形态保留,
  // 单点量化台阶被柔化成缓坡。不要加大核——更狠的平滑会抹掉
  // 持续 10 分钟级的真实特征(如压迫性假低的 V 形)。
  const smoothedData = chartData.map((reading, index, all) => {
    const prev = all[index - 1]?.glucose_mmol ?? reading.glucose_mmol
    const next = all[index + 1]?.glucose_mmol ?? reading.glucose_mmol
    return {
      ...reading,
      glucose_line: (prev + 2 * reading.glucose_mmol + next) / 4,
    }
  })
  const dataBounds: TimeRange | null = chartData.length
    ? {
        start: chartData[0].timestamp_ms,
        end: chartData.at(-1)!.timestamp_ms,
      }
    : null
  const domainEnd = customRange
    ? customRange.end
    : rangeHours === 0
      ? (chartData.at(-1)?.timestamp_ms ?? 0)
      : (windowEnd ?? chartData.at(-1)?.timestamp_ms ?? 0)
  const domainStart = customRange
    ? customRange.start
    : rangeHours === 0
      ? (chartData[0]?.timestamp_ms ?? 0)
      : domainEnd - rangeHours * 60 * 60_000
  const rangeLabel = customRange
    ? "自定义范围"
    : rangeHours === 0
      ? "全部记录"
      : `最近 ${rangeHours} 小时`
  const axisTicks = Array.from(
    { length: 7 },
    (_, index) => domainStart + ((domainEnd - domainStart) * index) / 6
  )

  return (
    <Card className="@container/card">
      <CardHeader>
        <CardTitle>血糖趋势</CardTitle>
        <CardDescription>{rangeLabel}</CardDescription>
        <CardAction>
          <div className="hidden items-center gap-2 sm:flex">
            <CustomRangePicker
              customRange={customRange}
              bounds={dataBounds}
              onApply={onCustomRangeChange}
            />
            <RangeToggle
              rangeHours={rangeHours}
              customRange={customRange}
              onRangeChange={onRangeChange}
              className="*:data-[slot=toggle-group-item]:px-3"
            />
          </div>
        </CardAction>
      </CardHeader>
      <CardContent className="px-2 pt-4 sm:px-6 sm:pt-6">
        <div className="mb-4 flex items-center gap-2 px-2 sm:hidden">
          <CustomRangePicker
            customRange={customRange}
            bounds={dataBounds}
            onApply={onCustomRangeChange}
          />
          <RangeToggle
            rangeHours={rangeHours}
            customRange={customRange}
            onRangeChange={onRangeChange}
            className="w-full *:data-[slot=toggle-group-item]:flex-1"
          />
        </div>
        {loading ? (
          <Skeleton className="h-[280px] w-full" />
        ) : readings.length ? (
          <ChartContainer
            config={chartConfig}
            className="aspect-auto h-[280px] w-full sm:h-[340px]"
          >
            <LineChart
              accessibilityLayer
              data={smoothedData}
              margin={{ top: 8, right: 36, bottom: 0, left: 0 }}
            >
              <ReferenceArea
                y1={TARGET_GLUCOSE_RANGE.min}
                y2={TARGET_GLUCOSE_RANGE.max}
                fill="var(--muted-foreground)"
                fillOpacity={0.1}
                stroke="none"
              />
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="timestamp_ms"
                type="number"
                scale="time"
                domain={[domainStart, domainEnd]}
                allowDataOverflow
                tickLine={false}
                axisLine={false}
                tickMargin={10}
                minTickGap={40}
                ticks={axisTicks}
                tickFormatter={formatAxisTime}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                width={28}
                domain={[
                  0,
                  (dataMax: number) =>
                    Math.max(12, Math.ceil(Number(dataMax) + 1)),
                ]}
              />
              <ReferenceLine
                y={TARGET_GLUCOSE_RANGE.min}
                stroke="transparent"
                label={{
                  value: TARGET_GLUCOSE_RANGE.min,
                  position: "right",
                  offset: 6,
                  fill: "var(--destructive)",
                  fontSize: 12,
                  fontWeight: 600,
                }}
              />
              <ReferenceLine
                y={TARGET_GLUCOSE_RANGE.max}
                stroke="transparent"
                label={{
                  value: TARGET_GLUCOSE_RANGE.max,
                  position: "right",
                  offset: 6,
                  fill: "var(--glucose-high-label)",
                  fontSize: 12,
                  fontWeight: 600,
                }}
              />
              <ChartTooltip
                cursor={false}
                content={
                  <ChartTooltipContent
                    indicator="line"
                    formatter={(_, __, item) => {
                      // ChartTooltipContent 在 formatter 模式下会跳过默认的
                      // 时间标签渲染(nestLabel 分支),所以时间和真实值都在
                      // 这里输出。线条是平滑序列,值必须取原始字段。
                      const point = item.payload as
                        | (Reading & { timestamp_ms?: number })
                        | undefined
                      if (
                        !point ||
                        typeof point.timestamp_ms !== "number"
                      ) {
                        return ""
                      }
                      return (
                        <div className="grid gap-0.5">
                          <span className="font-medium">
                            {formatTooltipTime(point.timestamp_ms)}
                          </span>
                          <span className="font-mono font-medium tabular-nums">
                            {point.glucose_mmol.toFixed(1)} mmol/L
                          </span>
                        </div>
                      )
                    }}
                  />
                }
              />
              <Line
                dataKey="glucose_line"
                type="monotone"
                stroke="var(--color-glucose_mmol)"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ChartContainer>
        ) : (
          <Empty className="min-h-[280px]">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <ChartNoAxesCombinedIcon aria-hidden="true" />
              </EmptyMedia>
              <EmptyTitle>该时间范围内没有读数</EmptyTitle>
              <EmptyDescription>
                收到新的 BLE 广播记录后，趋势图会自动更新。
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
      </CardContent>
    </Card>
  )
}
