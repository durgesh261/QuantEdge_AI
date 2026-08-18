import { TrendingUp, Target, DollarSign, BarChart3, Clock } from 'lucide-react'

const stats = [
  { label: 'Total P&L', value: '+$12,450.00', change: '+12.5%', icon: DollarSign, positive: true },
  { label: 'Win Rate', value: '68%', change: '+3.2%', icon: Target, positive: true },
  { label: 'Active Trades', value: '1', change: '0', icon: TrendingUp, positive: true },
  { label: 'Open Positions', value: '2', change: '-1', icon: Briefcase, positive: false },
]

const recentActivity = [
  { time: '2 min ago', type: 'BUY', symbol: 'BTCUSD.P', price: '43,250.00', status: 'FILLED' },
  { time: '15 min ago', type: 'SELL', symbol: 'ETHUSD.P', price: '2,580.00', status: 'FILLED' },
  { time: '1 hour ago', type: 'BUY', symbol: 'SOLUSD.P', price: '98.50', status: 'PENDING' },
  { time: '3 hours ago', type: 'SELL', symbol: 'XRPUSD.P', price: '0.62', status: 'CANCELLED' },
]

export function Dashboard() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Dashboard</h1>
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <span className="flex items-center gap-1 text-green-400">
            <span className="w-2 h-2 rounded-full bg-green-400" />
            Connected
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((stat) => (
          <div key={stat.label} className="bg-slate-900 border border-slate-800 rounded-xl p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-400">{stat.label}</p>
                <p className="text-2xl font-bold text-white mt-1">{stat.value}</p>
                <p className={`text-sm mt-1 ${stat.positive ? 'text-green-400' : 'text-red-400'}`}>
                  {stat.change}
                </p>
              </div>
              <div className="p-3 bg-slate-800 rounded-lg">
                <stat.icon size={24} className="text-blue-400" />
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Recent Activity</h2>
          <div className="space-y-3">
            {recentActivity.map((activity, index) => (
              <div key={index} className="flex items-center justify-between py-3 border-b border-slate-800 last:border-0">
                <div className="flex items-center gap-4">
                  <span className="text-sm text-slate-400 w-20">{activity.time}</span>
                  <span
                    className={`px-2 py-1 text-xs font-medium rounded ${
                      activity.type === 'BUY' ? 'bg-green-900/30 text-green-400' : 'bg-red-900/30 text-red-400'
                    }`}
                  >
                    {activity.type}
                  </span>
                  <span className="font-mono text-sm text-white">{activity.symbol}</span>
                  <span className="text-sm text-slate-300 font-mono">${activity.price}</span>
                </div>
                <span
                  className={`px-2 py-1 text-xs font-medium rounded ${
                    activity.status === 'FILLED' ? 'bg-green-900/30 text-green-400' :
                    activity.status === 'PENDING' ? 'bg-yellow-900/30 text-yellow-400' :
                    'bg-slate-800 text-slate-400'
                  }`}
                >
                  {activity.status}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Quick Actions</h2>
          <div className="grid grid-cols-2 gap-3">
            <button className="p-4 bg-slate-800 border border-slate-700 rounded-lg hover:border-blue-500 hover:bg-slate-700 transition-colors text-left">
              <TrendingUp className="text-blue-400 mb-2" size={24} />
              <p className="font-medium text-white">Start Paper Trading</p>
              <p className="text-sm text-slate-400">Simulate strategies risk-free</p>
            </button>
            <button className="p-4 bg-slate-800 border border-slate-700 rounded-lg hover:border-green-500 hover:bg-slate-700 transition-colors text-left">
              <Target className="text-green-400 mb-2" size={24} />
              <p className="font-medium text-white">Configure Strategy</p>
              <p className="text-sm text-slate-400">Adjust SMC parameters</p>
            </button>
            <button className="p-4 bg-slate-800 border border-slate-700 rounded-lg hover:border-purple-500 hover:bg-slate-700 transition-colors text-left">
              <BarChart3 className="text-purple-400 mb-2" size={24} />
              <p className="font-medium text-white">View Analytics</p>
              <p className="text-sm text-slate-400">Performance metrics</p>
            </button>
            <button className="p-4 bg-slate-800 border border-slate-700 rounded-lg hover:border-orange-500 hover:bg-slate-700 transition-colors text-left">
              <Settings className="text-orange-400 mb-2" size={24} />
              <p className="font-medium text-white">Settings</p>
              <p className="text-sm text-slate-400">Account & preferences</p>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}