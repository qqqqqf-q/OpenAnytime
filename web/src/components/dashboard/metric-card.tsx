import type { ReactNode } from "react"

import {
  Card,
  CardAction,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

type MetricCardProps = {
  label: string
  value: string
  unit: string
  className?: string
  action?: ReactNode
  loading: boolean
}

export function MetricCard({
  label,
  value,
  unit,
  className,
  action,
  loading,
}: MetricCardProps) {
  return (
    <Card className={cn("@container/card", className)}>
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
    </Card>
  )
}
