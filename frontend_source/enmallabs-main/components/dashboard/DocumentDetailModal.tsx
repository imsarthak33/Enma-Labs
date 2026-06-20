'use client';

import { motion, AnimatePresence } from 'framer-motion';
import { X, FileText, Calendar, IndianRupee, Building2, Hash, Shield, AlertTriangle } from 'lucide-react';
import { StatusBadge } from './StatusBadge';
import { formatCurrency, formatRelativeTime, formatDocType } from '@/hooks/use-dashboard-data';
import type { DocumentRecord } from '@/hooks/use-dashboard-data';

interface DocumentDetailModalProps {
  document: DocumentRecord | null;
  onClose: () => void;
}

export function DocumentDetailModal({ document: doc, onClose }: DocumentDetailModalProps) {
  if (!doc) return null;

  const extracted = doc.extraction_data?.invoice || doc.extraction_data || {};
  const lineItems = extracted?.line_items || [];
  const taxSlabs = extracted?.tax_slabs || [];
  const itcEligible = extracted?.itc_eligible ?? null;
  const processingError = extracted?.processing_error || null;


  return (
    <AnimatePresence>
      {doc && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50"
            onClick={onClose}
          />
          <motion.div
            initial={{ opacity: 0, x: 40 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 40 }}
            transition={{ type: 'spring', damping: 25, stiffness: 300 }}
            className="fixed right-0 top-0 bottom-0 w-full max-w-lg bg-black/95 backdrop-blur-xl border-l border-white/10 z-50 flex flex-col overflow-hidden"
          >
            {/* Header */}
            <div className="flex items-center justify-between p-6 border-b border-white/5">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center">
                  <FileText className="w-5 h-5 text-accent" />
                </div>
                <div>
                  <h2 className="font-serif text-lg font-medium">Document Details</h2>
                  <p className="text-xs text-white/30">{doc.original_file_name}</p>
                </div>
              </div>
              <button onClick={onClose} className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center text-white/40 hover:text-white hover:bg-white/10 transition-all">
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto custom-scrollbar p-6 space-y-6">
              {/* Status & Type */}
              <div className="flex items-center gap-3">
                <StatusBadge status={doc.processing_status} size="md" />
                <StatusBadge status={doc.document_type || 'unknown'} size="md" />
              </div>

              {/* Key Info Grid */}
              <div className="grid grid-cols-2 gap-4">
                {doc.vendor_name && (
                  <InfoCard icon={Building2} label="Vendor" value={doc.vendor_name} />
                )}
                {doc.buyer_name && (
                  <InfoCard icon={Building2} label="Buyer" value={doc.buyer_name} />
                )}
                {doc.total_amount && (
                  <InfoCard icon={IndianRupee} label="Total Amount" value={formatCurrency(doc.total_amount)} />
                )}
                {doc.invoice_number && (
                  <InfoCard icon={Hash} label="Invoice #" value={doc.invoice_number} />
                )}
                <InfoCard icon={Calendar} label="Received" value={formatRelativeTime(doc.created_at)} />
                {doc.vendor_gstin && (
                  <InfoCard icon={Shield} label="Vendor GSTIN" value={doc.vendor_gstin} />
                )}
              </div>

              {/* ITC Eligibility */}
              {itcEligible !== null && (
                <div className={`p-4 rounded-2xl border ${itcEligible ? 'bg-emerald-500/5 border-emerald-500/20' : 'bg-red-500/5 border-red-500/20'}`}>
                  <div className="flex items-center gap-2 mb-1">
                    {itcEligible ? (
                      <Shield className="w-4 h-4 text-emerald-400" />
                    ) : (
                      <AlertTriangle className="w-4 h-4 text-red-400" />
                    )}
                    <span className={`text-xs font-bold uppercase tracking-wider ${itcEligible ? 'text-emerald-400' : 'text-red-400'}`}>
                      {itcEligible ? 'ITC Eligible' : 'ITC Not Eligible'}
                    </span>
                  </div>
                  {extracted?.itc_reason && (
                    <p className="text-xs text-white/40 mt-1">{extracted.itc_reason}</p>
                  )}
                </div>
              )}

              {/* Line Items */}
              {lineItems.length > 0 && (
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-wider text-white/40 mb-3">Line Items</h3>
                  <div className="glass-card rounded-2xl overflow-hidden">
                    <div className="divide-y divide-white/5">
                      {lineItems.map((item: any, i: number) => (
                        <div key={i} className="px-4 py-3 flex items-center justify-between">
                          <div>
                            <p className="text-sm text-white/80">{item.item_name}</p>
                            {item.hsn_code && <p className="text-[10px] text-white/30 mt-0.5">HSN: {item.hsn_code}</p>}
                          </div>
                          <div className="text-right">
                            <p className="text-sm font-medium">{item.total_amount ? formatCurrency(item.total_amount) : '—'}</p>
                            {item.cgst_rate && (
                              <p className="text-[10px] text-white/30">GST: {(item.cgst_rate || 0) + (item.sgst_rate || 0)}%</p>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Tax Slabs */}
              {taxSlabs.length > 0 && (
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-wider text-white/40 mb-3">Tax Breakdown</h3>
                  <div className="glass-card rounded-2xl p-4 space-y-3">
                    {taxSlabs.map((slab: any, i: number) => (
                      <div key={i} className="space-y-2">
                        <div className="flex justify-between text-xs">
                          <span className="text-white/40">Taxable Value</span>
                          <span className="text-white/80">{slab.taxable_value ? formatCurrency(slab.taxable_value) : '—'}</span>
                        </div>
                        {slab.cgst_amount && (
                          <div className="flex justify-between text-xs">
                            <span className="text-white/40">CGST ({slab.cgst_rate}%)</span>
                            <span className="text-white/80">{formatCurrency(slab.cgst_amount)}</span>
                          </div>
                        )}
                        {slab.sgst_amount && (
                          <div className="flex justify-between text-xs">
                            <span className="text-white/40">SGST ({slab.sgst_rate}%)</span>
                            <span className="text-white/80">{formatCurrency(slab.sgst_amount)}</span>
                          </div>
                        )}
                        {slab.igst_amount && (
                          <div className="flex justify-between text-xs">
                            <span className="text-white/40">IGST ({slab.igst_rate}%)</span>
                            <span className="text-white/80">{formatCurrency(slab.igst_amount)}</span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Processing Error */}
              {processingError && (
                <div className="p-4 rounded-2xl bg-red-500/5 border border-red-500/20">
                  <div className="flex items-center gap-2 mb-2">
                    <AlertTriangle className="w-4 h-4 text-red-400" />
                    <span className="text-xs font-bold text-red-400 uppercase tracking-wider">Processing Error</span>
                  </div>
                  <p className="text-xs text-white/50">{processingError}</p>
                </div>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

function InfoCard({ icon: Icon, label, value }: { icon: any; label: string; value: string }) {
  return (
    <div className="bg-white/[0.02] border border-white/5 rounded-xl p-3">
      <div className="flex items-center gap-2 mb-1">
        <Icon className="w-3.5 h-3.5 text-white/20" />
        <span className="text-[10px] font-bold uppercase tracking-wider text-white/30">{label}</span>
      </div>
      <p className="text-sm text-white/80 truncate">{value}</p>
    </div>
  );
}
