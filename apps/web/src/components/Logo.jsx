import { Zap } from 'lucide-react'

/** The LiveRAG mark: a lavender square with a lightning bolt, plus the name. */
export default function Logo({ size = 'md', showName = true }) {
  const box = size === 'lg' ? 'h-10 w-10 rounded-xl' : 'h-8 w-8 rounded-lg'
  const icon = size === 'lg' ? 20 : 16
  return (
    <div className="flex items-center gap-2.5">
      <div className={`${box} flex items-center justify-center bg-brand-600 text-brand-50`}>
        <Zap size={icon} strokeWidth={2.4} />
      </div>
      {showName && (
        <span className={`font-semibold tracking-tight ${size === 'lg' ? 'text-xl' : 'text-base'}`}>
          LiveRAG
        </span>
      )}
    </div>
  )
}
