import { ChartNoAxesCombinedIcon } from "lucide-react"
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceArea,
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

const chartConfig = {
  glucose_mmol: {
    label: "血糖 mmol/L",
    color: "var(--primary)",
  },
} satisfies ChartConfig

function formatAxisTime(timestamp: string) {
  return new Date(timestamp).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatTooltipTime(timestamp: string) {
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
  loading,
  onRangeChange,
}: GlucoseChartProps) {
  return (
    <Card className="@container/card">
      <CardHeader>
        <CardTitle>血糖趋势</CardTitle>
        <CardDescription>
          最近 {rangeHours} 小时，参考范围 3.9–7.8 mmol/L
        </CardDescription>
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
            <AreaChart
              accessibilityLayer
              data={readings}
              margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
            >
              <defs>
                <linearGradient id="fillGlucose" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="5%"
                    stopColor="var(--color-glucose_mmol)"
                    stopOpacity={0.35}
                  />
                  <stop
                    offset="95%"
                    stopColor="var(--color-glucose_mmol)"
                    stopOpacity={0.03}
                  />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="timestamp"
                tickLine={false}
                axisLine={false}
                tickMargin={10}
                minTickGap={40}
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
              <ReferenceArea
                y1={3.9}
                y2={7.8}
                fill="var(--muted-foreground)"
                fillOpacity={0.08}
                stroke="transparent"
              />
              <ChartTooltip
                cursor={false}
                content={
                  <ChartTooltipContent
                    indicator="line"
                    labelFormatter={(label) => formatTooltipTime(String(label))}
                  />
                }
              />
              <Area
                dataKey="glucose_mmol"
                type="natural"
                fill="url(#fillGlucose)"
                stroke="var(--color-glucose_mmol)"
                strokeWidth={2}
                activeDot={{ r: 4 }}
                isAnimationActive={false}
              />
            </AreaChart>
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
