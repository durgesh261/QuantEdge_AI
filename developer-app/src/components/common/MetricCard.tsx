import React from 'react'
import { LucideIcon } from 'lucide-react'

interface MetricCardProps {
  title: string
  value: string | number
  subtext?: string
  icon: LucideIcon
  variant?: 'emerald' | 'cyan' | 'amber' | 'purple' | 'blue' | 'default'
}

export const MetricCard: React.FC<MetricCardProps> = ({
  title,
  value,
  subtext,
  icon: Icon,
  variant = 'default',
}) => {
  const getGlow = () => {
    switch (variant) {
      case 'emerald':
        return 'border-dev-accent/30 hover:border-dev-accent/60'
      case 'cyan':
        return 'border-dev-cyan/30 hover:border-dev-cyan/60'
      case 'amber':
        return 'border-dev-amber/30 hover:border-dev-amber/60'
      case 'purple':
        return 'border-dev-purple/30 hover:border-dev-purple/60'
      case 'blue':
        return 'border-dev-blue/30 hover:border-dev-blue/60'
      default:
        return 'border-terminal-border hover:border-slate-600'
    }
  }

  const getIconColor = () => {
    switch (variant) {
      case 'emerald':
        return 'text-dev-accent bg-dev-accent/10'
      case 'cyan':
        return 'text-dev-cyan bg-dev-cyan/10'
      case 'amber':
        return 'text-dev-amber bg-dev-amber/10'
      case 'purple':
        return 'text-dev-purple bg-dev-purple/10'
      case 'blue':
        return 'text-dev-blue bg-dev-blue/10'
      default:
        return 'text-slate-400 bg-slate-800'
    }
  }

  return (
    <div className={`glass-panel p-4 rounded-lg border transition-all ${getGlow()}`}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-mono text-slate-400 uppercase tracking-wider">{title}</span>
        <div className={`p-2 rounded-md ${getIconColor()}`}>
          <Icon className="w-4 h-4" />
        </div>
      </div>
      <div className="mt-2">
        <div className="text-xl font-bold text-white font-mono tracking-tight">{value}</div>
        {subtext && <div className="text-[11px] font-mono text-slate-400 mt-1">{subtext}</div>}
      </div>
    </div>
  )
}
