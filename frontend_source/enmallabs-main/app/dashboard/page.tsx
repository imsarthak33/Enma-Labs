'use client';

import { useDashboard } from './dashboard-context';
import { useDashboardStats, useRecentActivity, useDocuments, formatRelativeTime, formatDocType } from '@/hooks/use-dashboard-data';
import type { DocumentFilters } from '@/hooks/use-dashboard-data';
import { StatsCard } from '@/components/dashboard/StatsCard';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { EmptyState } from '@/components/dashboard/EmptyState';
import { DocumentTypeChart, IntentTagChart, DocumentTimelineChart, ProcessingStatusChart } from '@/components/dashboard/Charts';
import { motion } from 'framer-motion';
import { FileText, MessageCircle, CheckCircle2, Clock, Send, AlertCircle, ChevronRight, Users, IndianRupee, Zap } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

const emptyFilters: DocumentFilters = { search: '', documentType: null, processingStatus: null, dateFrom: null, dateTo: null, vendor: null };

export default function DashboardOverview() {
  const { firmData, firmLoading } = useDashboard();
  const { stats, loading: statsLoading } = useDashboardStats(firmData?.id || null);
  const { activities, loading: activityLoading } = useRecentActivity(firmData?.id || null, 8);
  const { documents } = useDocuments(firmData?.id || null, emptyFilters);
  const loading = firmLoading || statsLoading;

  return (
    <div className="max-w-7xl mx-auto">
      {/* Page Header */}
      <div className="flex items-center justify-between mb-10">
        <div>
          <h1 className="font-serif text-3xl font-medium mb-2">{firmData?.firm_name || 'Dashboard'}</h1>
          <p className="text-white/40 text-sm">Welcome back, {firmData?.ca_name || 'Admin'}. Here&apos;s your firm&apos;s activity overview.</p>
        </div>
        <Link href="/dashboard/clients" className="hidden sm:flex items-center gap-2 px-5 py-2.5 rounded-full bg-accent/20 border border-accent/30 text-white text-[13px] font-bold hover:bg-accent/30 hover:border-accent/50 transition-all active:scale-95">
          <Users className="w-4 h-4" />View Clients
        </Link>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-10">
        <StatsCard label="Total Documents" value={stats?.totalDocuments || 0} icon={FileText} color="text-blue-400" loading={loading} />
        <StatsCard label="Chat Interactions" value={stats?.totalChatLogs || 0} icon={MessageCircle} color="text-accent" loading={loading} />
        <StatsCard label="Processing Rate" value={stats?.processingRate || 0} icon={CheckCircle2} color="text-emerald-400" suffix="%" loading={loading} />
        <StatsCard label="Total Value" value={stats?.totalAmount ? Math.round(stats.totalAmount) : 0} icon={IndianRupee} color="text-amber-400" prefix="₹" loading={loading} />
      </div>

      {/* No data state */}
      {!loading && stats?.totalDocuments === 0 && (
        <div className="mb-10">
          <div className="glass-card rounded-[24px] p-8 border-accent/20 bg-accent/[0.02]">
            <div className="flex items-start gap-6">
              <div className="w-14 h-14 rounded-2xl bg-accent/10 flex items-center justify-center border border-accent/20 shrink-0"><Zap className="w-7 h-7 text-accent" /></div>
              <div>
                <h3 className="font-serif text-xl font-medium mb-2">Get Started with Enma</h3>
                <p className="text-sm text-white/50 leading-relaxed mb-4">Your dashboard will come alive once you start processing documents.</p>
                <div className="space-y-3">
                  <Step num="1" text="Connect your Telegram account to Enma bot" done={!!firmData?.telegram_chat_id} />
                  <Step num="2" text="Forward invoices, bills, or bank statements to the bot" done={false} />
                  <Step num="3" text="Enma will automatically extract data and show it here" done={false} />
                </div>
                {!firmData?.telegram_chat_id && (
                  <a href="https://t.me/enma12bot" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 mt-6 px-5 py-2.5 rounded-full bg-[#229ED9]/10 border border-[#229ED9]/20 text-[#229ED9] text-sm font-bold hover:bg-[#229ED9]/20 transition-all">
                    <Send className="w-4 h-4" />Connect Telegram
                  </a>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Charts Grid — only show when there's data */}
      {stats && stats.totalDocuments > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-10">
          <DocumentTimelineChart documents={documents.map(d => ({ created_at: d.created_at, total_amount: d.total_amount }))} />
          <ProcessingStatusChart stats={stats} />
          <DocumentTypeChart stats={stats} />
          <IntentTagChart stats={stats} />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Recent Activity */}
        <div className="lg:col-span-2">
          <div className="flex items-center justify-between mb-6 px-1">
            <h2 className="font-serif text-xl font-medium">Recent Activity</h2>
            <Link href="/dashboard/documents" className="text-xs font-bold text-accent uppercase tracking-widest hover:text-accent-hover transition-colors">View All</Link>
          </div>
          {activityLoading ? (
            <div className="glass-card rounded-[24px] overflow-hidden">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="p-5 border-b border-white/[0.03] flex items-center gap-4">
                  <div className="w-10 h-10 rounded-full bg-white/5 animate-pulse" />
                  <div className="flex-1 space-y-2"><div className="h-3.5 w-32 bg-white/10 rounded animate-pulse" /><div className="h-3 w-48 bg-white/5 rounded animate-pulse" /></div>
                  <div className="h-5 w-16 bg-white/5 rounded-full animate-pulse" />
                </div>
              ))}
            </div>
          ) : activities.length > 0 ? (
            <div className="glass-card rounded-[24px] overflow-hidden">
              <div className="divide-y divide-white/5">
                {activities.map((activity, i) => (
                  <motion.div key={activity.id} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.05 }} className="p-5 flex items-center justify-between hover:bg-white/[0.02] transition-all group cursor-default">
                    <div className="flex items-center gap-4 min-w-0">
                      <div className={`w-10 h-10 rounded-full flex items-center justify-center border shrink-0 ${activity.type === 'document' ? 'bg-blue-500/10 border-blue-500/20' : 'bg-violet-500/10 border-violet-500/20'}`}>
                        {activity.type === 'document' ? <FileText className="w-5 h-5 text-blue-400" /> : <MessageCircle className="w-5 h-5 text-violet-400" />}
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-white group-hover:text-accent transition-colors truncate">{activity.title}</p>
                        <p className="text-xs text-white/40 truncate">{activity.description}</p>
                      </div>
                    </div>
                    <div className="text-right shrink-0 ml-4">
                      <StatusBadge status={activity.status} />
                      <p className="text-[10px] text-white/20 mt-1">{formatRelativeTime(activity.timestamp)}</p>
                    </div>
                  </motion.div>
                ))}
              </div>
            </div>
          ) : (
            <div className="glass-card rounded-[24px] p-12"><EmptyState icon={Clock} title="No Activity Yet" description="Activity will appear here as you process documents and interact with the Enma bot." /></div>
          )}
        </div>

        {/* Right Sidebar */}
        <div className="space-y-6">
          {/* Bot Status */}
          <div>
            <h2 className="font-serif text-xl font-medium mb-4 px-1">Bot Status</h2>
            <div className="glass-card rounded-[24px] p-6 relative overflow-hidden">
              <div className="absolute top-0 right-0 w-32 h-32 bg-emerald-500/5 blur-3xl rounded-full" />
              <div className="flex items-center gap-4 mb-6">
                <div className="relative">
                  <div className={`w-12 h-12 rounded-2xl flex items-center justify-center border ${firmData?.telegram_chat_id ? 'bg-emerald-500/10 border-emerald-500/20' : 'bg-white/5 border-white/10'}`}>
                    <MessageCircle className={`w-6 h-6 ${firmData?.telegram_chat_id ? 'text-emerald-400' : 'text-white/20'}`} />
                  </div>
                  {firmData?.telegram_chat_id && <div className="absolute -top-1 -right-1 w-4 h-4 bg-emerald-500 rounded-full border-4 border-black animate-pulse" />}
                </div>
                <div>
                  <h3 className="font-bold text-white text-sm">{firmData?.telegram_chat_id ? 'Enma AI Active' : 'Not Connected'}</h3>
                  <p className={`text-xs font-medium ${firmData?.telegram_chat_id ? 'text-emerald-400/60' : 'text-white/30'}`}>{firmData?.telegram_chat_id ? 'System operational' : 'Connect Telegram to activate'}</p>
                </div>
              </div>
              {stats && (
                <div className="space-y-3 mb-6">
                  <div className="flex justify-between text-xs"><span className="text-white/40">Documents Processed</span><span className="text-white/80">{stats.extractedDocuments}</span></div>
                  <div className="flex justify-between text-xs"><span className="text-white/40">Success Rate</span><span className="text-white/80">{stats.processingRate}%</span></div>
                  <div className="flex justify-between text-xs"><span className="text-white/40">Failed</span><span className={stats.failedDocuments > 0 ? 'text-red-400' : 'text-white/80'}>{stats.failedDocuments}</span></div>
                </div>
              )}
              <a href="https://t.me/enma12bot" target="_blank" rel="noopener noreferrer" className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-[#229ED9]/10 border border-[#229ED9]/20 text-[#229ED9] text-xs font-bold hover:bg-[#229ED9]/20 transition-all">
                <Send className="w-3.5 h-3.5" />{firmData?.telegram_chat_id ? 'Open Bot' : 'Connect Telegram'}
              </a>
            </div>
          </div>

          {/* Quick Tip */}
          <div className="glass-card rounded-[24px] p-6 bg-accent/[0.03] border-accent/20">
            <div className="flex items-center gap-3 mb-3"><AlertCircle className="w-5 h-5 text-accent" /><h3 className="font-bold text-white text-sm">Quick Tip</h3></div>
            <p className="text-xs text-white/50 leading-relaxed mb-4">Forward invoices and bank statements directly to the Enma Telegram bot for instant extraction and reconciliation.</p>
            <Link href="/dashboard/chat" className="flex items-center gap-2 text-xs font-bold text-accent hover:text-accent-hover transition-all group">
              Open Chat<ChevronRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

function Step({ num, text, done }: { num: string; text: string; done: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 ${done ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-white/5 text-white/30 border border-white/10'}`}>{done ? '✓' : num}</div>
      <span className={`text-sm ${done ? 'text-emerald-400/60 line-through' : 'text-white/50'}`}>{text}</span>
    </div>
  );
}
