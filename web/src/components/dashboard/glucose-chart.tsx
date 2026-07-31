import { ChartNoAxesCombinedIcon } from "lucide-react"
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts"

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
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { Reading } from "@/lib/api"

const RANGE_OPTIONS = [3, 6, 12, 24] as const
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

type GlucoseChartProps = {
  readings: Reading[]
  rangeHours: number
  windowEnd: number | null
  loading: boolean
  onRangeChange: (rangeHours: number) => void
}

function RangeToggle({
  rangeHours,
  onRangeChange,
  className,
}: {
  rangeHours: number
  onRangeChange: (rangeHours: number) => void
  className?: string
}) {
  return (
    <ToggleGroup
      multiple={false}
      value={[String(rangeHours)]}
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
          {hours}h
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

export function GlucoseChart({
  readings,
  rangeHours,
  windowEnd,
  loading,
  onRangeChange,
}: GlucoseChartProps) {
  // A categorical timestamp axis stretches sparse samples across the card. A
  // numeric domain keeps the selected range tied to actual wall-clock time.
  const chartData = readings
    .map((reading) => ({
      ...reading,
      timestamp_ms: new Date(reading.timestamp).getTime(),
    }))
    .filter((reading) => Number.isFinite(reading.timestamp_ms))
  const domainEnd = windowEnd ?? chartData.at(-1)?.timestamp_ms ?? 0
  const domainStart = domainEnd - rangeHours * 60 * 60_000
  const axisTicks = Array.from(
    { length: 7 },
    (_, index) => domainStart + ((domainEnd - domainStart) * index) / 6
  )

  return (
    <Card className="@container/card">
      <CardHeader>
        <CardTitle>血糖趋势</CardTitle>
        <CardDescription>最近 {rangeHours} 小时</CardDescription>
        <CardAction>
          <RangeToggle
            rangeHours={rangeHours}
            onRangeChange={onRangeChange}
            className="hidden *:data-[slot=toggle-group-item]:px-3 sm:flex"
          />
        </CardAction>
      </CardHeader>
      <CardContent className="px-2 pt-4 sm:px-6 sm:pt-6">
        <div className="mb-4 px-2 sm:hidden">
          <RangeToggle
            rangeHours={rangeHours}
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
              data={chartData}
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
                    labelFormatter={(_, payload) => {
                      const timestamp = payload[0]?.payload?.timestamp_ms
                      return typeof timestamp === "number"
                        ? formatTooltipTime(timestamp)
                        : ""
                    }}
                  />
                }
              />
              <Line
                dataKey="glucose_mmol"
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
