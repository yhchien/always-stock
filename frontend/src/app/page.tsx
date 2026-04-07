import IndustryDashboard from "@/components/IndustryDashboard"

// Default to today's date in YYYY-MM-DD format
function todayString() {
  return new Date().toISOString().slice(0, 10)
}

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8">
      <IndustryDashboard defaultDate={todayString()} />
    </main>
  )
}
