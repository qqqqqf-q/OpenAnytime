import { ChartNoAxesCombinedIcon } from "lucide-react"
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  XAxis,
  YAxis,
} from "recharts"

import { Badge } from "@/components/ui/badge"
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
import type { Reading } from "@/lib/api"

const chartConfig = {
  glucose_mmol: {
    label: "血糖 mmol/L",
    color: "var(--chart-1)",
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
}

export function GlucoseChart({
  readings,
  rangeHours,
  loading,
}: GlucoseChartProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>血糖趋势</CardTitle>
        <CardDescription>最近 {rangeHours} 小时的广播解码结果</CardDescription>
        <CardAction>
          <Badge variant="success">目标范围 3.9–7.8</Badge>
        </CardAction>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-80 w-full" />
        ) : readings.length ? (
          <ChartContainer
            config={chartConfig}
            className="aspect-auto h-80 w-full sm:h-96"
          >
            <LineChart
              accessibilityLayer
              data={readings}
              margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
            >
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
                fill="var(--success)"
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
          <Empty className="min-h-80">
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
