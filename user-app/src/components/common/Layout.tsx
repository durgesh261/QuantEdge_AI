import React from 'react'
import { Header } from './Header'
import { Sidebar } from './Sidebar'
import { Footer } from './Footer'
import { ConnectivityBanner } from './ConnectivityBanner'
import { ToastContainer } from './ToastContainer'

interface LayoutProps {
  children: React.ReactNode
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  return (
    <div className="h-screen w-screen flex flex-col bg-background text-slate-100 overflow-hidden relative">
      <ConnectivityBanner />
      <Header />
      <div className="flex-1 flex overflow-hidden min-h-0">
        <Sidebar />
        <main className="flex-1 h-full overflow-y-auto p-4 md:p-6 bg-background flex flex-col justify-between">
          <div className="max-w-[1600px] w-full mx-auto pb-6">{children}</div>
          <Footer />
        </main>
      </div>
      <ToastContainer />
    </div>
  )
}
