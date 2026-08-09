import React, { useState, useRef, useEffect } from 'react';
import { Send, Bot, Sparkles, Trash2, Loader2 } from 'lucide-react';
import { apiClient as api } from '../../services/api';
import { useTerminalStore } from '../../store/useTerminalStore';

interface ChatMessage {
  role: 'user' | 'copilot';
  content: string;
  timestamp: string;
}

export const TradeCopilot: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: 'copilot',
      content: "Hey! I'm QuantEdge Copilot. I know your live positions, scanner decisions, order blocks, and strategy rules. Ask me anything — 'Why did we take this trade?', 'What's the market structure?', or 'Should I enter now?'",
      timestamp: new Date().toISOString(),
    },
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { activeSymbol } = useTerminalStore();

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  const sendMessage = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMsg: ChatMessage = {
      role: 'user',
      content: input.trim(),
      timestamp: new Date().toISOString(),
    };

    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setIsLoading(true);

    try {
      const res = await api.post('/copilot/chat', {
        message: userMsg.content,
        activeSymbol,
        userId: 'default-user',
      });

      if (res.data?.success && res.data.data) {
        const copilotMsg: ChatMessage = {
          role: 'copilot',
          content: res.data.data.content,
          timestamp: res.data.data.timestamp,
        };
        setMessages(prev => [...prev, copilotMsg]);
      } else {
        throw new Error('Invalid response');
      }
    } catch (err: any) {
      const errorMsg: ChatMessage = {
        role: 'copilot',
        content: `Sorry, I couldn't process that. ${err.response?.data?.error || 'Backend connection error. Is the server running?'}`, 
        timestamp: new Date().toISOString(),
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  const clearChat = () => {
    api.delete('/copilot/history').catch(() => {});
    setMessages([
      {
        role: 'copilot',
        content: "Chat cleared. Ask me about your trades, market structure, or strategy rules.",
        timestamp: new Date().toISOString(),
      },
    ]);
  };

  return (
    <div className="flex flex-col h-full bg-[#0E121A] w-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-[#1E293B] shrink-0">
        <div className="flex items-center space-x-2">
          <div className="w-6 h-6 rounded-full bg-[#00C896]/20 flex items-center justify-center">
            <Sparkles className="w-3.5 h-3.5 text-[#00C896]" />
          </div>
          <div>
            <h3 className="text-[11px] font-bold text-[#F8FAFC]">TRADE COPILOT</h3>
            <p className="text-[9px] text-[#94A3B8]">Strategy-Aware AI Assistant</p>
          </div>
        </div>
        <div className="flex items-center space-x-1">
          <div className="w-1.5 h-1.5 rounded-full bg-[#00C896] animate-pulse" />
          <span className="text-[9px] text-[#00C896] font-bold">Online</span>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[90%] rounded-lg p-2.5 text-[11px] leading-relaxed ${
              msg.role === 'user'
                ? 'bg-[#3B82F6] text-white'
                : 'bg-[#161D2A] border border-[#1E293B] text-[#E2E8F0]'
            }`}>
              {msg.role === 'copilot' && (
                <div className="flex items-center space-x-1.5 mb-1.5">
                  <Bot className="w-3 h-3 text-[#00C896]" />
                  <span className="text-[9px] font-bold text-[#00C896] uppercase tracking-wider">Copilot</span>
                </div>
              )}
              <div className="whitespace-pre-line">{msg.content}</div>
              <div className={`text-[8px] mt-1.5 ${msg.role === 'user' ? 'text-blue-200' : 'text-[#64748B]'}`}>
                {new Date(msg.timestamp).toLocaleTimeString()}
              </div>
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="flex justify-start">
            <div className="bg-[#161D2A] border border-[#1E293B] rounded-lg p-2.5">
              <div className="flex items-center space-x-2">
                <Loader2 className="w-3.5 h-3.5 text-[#00C896] animate-spin" />
                <span className="text-[10px] text-[#94A3B8]">Analyzing market data...</span>
              </div>
            </div>
          </div>
        )}
      </div>



      {/* Input */}
      <form onSubmit={sendMessage} className="p-3 border-t border-[#1E293B] shrink-0">
        <div className="flex items-center space-x-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask Copilot about trades, structure, rules..."
            className="flex-1 bg-[#0B0E14] border border-[#1E293B] rounded-lg px-3 py-2 text-[11px] text-[#F8FAFC] placeholder-[#64748B] focus:outline-none focus:border-[#3B82F6]"
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="p-2 bg-[#3B82F6] hover:bg-[#2563EB] disabled:bg-[#1E293B] disabled:text-[#64748B] text-white rounded-lg transition-colors"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
        <div className="flex items-center justify-between mt-1.5">
          <span className="text-[8px] text-[#64748B]">Connected to live backend data</span>
          <button
            type="button"
            onClick={clearChat}
            className="flex items-center space-x-1 text-[8px] text-[#64748B] hover:text-[#F6465D] transition-colors"
          >
            <Trash2 className="w-3 h-3" />
            <span>Clear</span>
          </button>
        </div>
      </form>
    </div>
  );
};
