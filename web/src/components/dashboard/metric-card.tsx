import type { LucideIcon } from "lucide-react"

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

type MetricCardProps = {
  icon: LucideIcon
  label: string
  value: string
  unit: string
  description: string
  loading: boolean
}

export function MetricCard({
  icon: Icon,
  label,
  value,
  unit,
  description,
  loading,
}: MetricCardProps) {
  return (
    <Card size="sm" className="min-w-0">
      <CardHeader>
        <CardTitle className="flex min-w-0 items-center gap-2">
          <Icon
            className="size-4 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
          <span className="truncate">{label}</span>
        </CardTitle>
        <CardDescription className="truncate">{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-28" />
        ) : (
          <div className="flex min-w-0 items-baseline gap-1.5">
            <span className="truncate text-2xl font-semibold tabular-nums sm:text-3xl">
              {value}
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {unit}
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
