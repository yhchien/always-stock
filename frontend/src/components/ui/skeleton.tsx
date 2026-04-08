function Skeleton({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`animate-pulse rounded-md bg-zinc-800 ${className}`}
      {...props}
    />
  )
}

export { Skeleton }
