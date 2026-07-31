import { ListIcon } from "lucide-react"

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
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { Reading } from "@/lib/api"

const MAX_VISIBLE_ROWS = 24

function formatTableTime(timestamp: string) {
  return new Date(timestamp).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

type ReadingsTableProps = {
  readings: Reading[]
  loading: boolean
}

export function ReadingsTable({ readings, loading }: ReadingsTableProps) {
  const rows = readings.slice(-MAX_VISIBLE_ROWS).reverse()

  return (
    <Card>
      <CardHeader>
        <CardTitle>最近读数</CardTitle>
        <CardDescription>按采集时间倒序排列</CardDescription>
        <CardAction>
          <Badge variant="outline">{readings.length} 条</Badge>
        </CardAction>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 6 }, (_, index) => (
              <Skeleton key={index} className="h-9 w-full" />
            ))}
          </div>
        ) : rows.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>编号</TableHead>
                <TableHead>血糖</TableHead>
                <TableHead className="hidden md:table-cell">温度</TableHead>
                <TableHead className="hidden lg:table-cell">信号</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((reading) => (
                <TableRow key={reading.id}>
                  <TableCell className="text-muted-foreground tabular-nums">
                    {formatTableTime(reading.timestamp)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    #{reading.reading_index ?? reading.counter ?? "--"}
                  </TableCell>
                  <TableCell className="font-medium tabular-nums">
                    {reading.glucose_mmol.toFixed(1)}
                  </TableCell>
                  <TableCell className="hidden tabular-nums md:table-cell">
                    {reading.temperature_c.toFixed(1)} °C
                  </TableCell>
                  <TableCell className="hidden tabular-nums lg:table-cell">
                    {reading.rssi ?? "--"} dBm
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Empty className="min-h-52">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <ListIcon aria-hidden="true" />
              </EmptyMedia>
              <EmptyTitle>暂无可显示的读数</EmptyTitle>
              <EmptyDescription>
                数据库中出现新记录后会在此列出。
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
      </CardContent>
    </Card>
  )
}
