import type { ReactNode } from "react"

import {
  Card,
  CardAction,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

type MetricCardProps = {
  label: string
  value: string
  unit: string
  summary: ReactNode
  description: string
  action?: ReactNode
  loading: boolean
}

export function MetricCard({
  label,
  value,
  unit,
  summary,
  description,
  action,
  loading,
}: MetricCardProps) {
  return (
    <Card className="@container/card">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        {loading ? (
          <Skeleton className="h-9 w-32" />
        ) : (
          <CardTitle className="flex items-baseline gap-1.5 text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            {value}
            <span className="text-xs font-normal text-muted-foreground">
              {unit}
            </span>
          </CardTitle>
        )}
        {action ? <CardAction>{action}</CardAction> : null}
      </CardHeader>
      <CardFooter className="flex-col items-start gap-1.5 text-sm">
        <div className="line-clamp-1 flex items-center gap-2 font-medium">
          {summary}
        </div>
        <div className="text-muted-foreground">{description}</div>
      </CardFooter>
    </Card>
  )
}
