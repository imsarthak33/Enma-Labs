'use client';

import Link from 'next/link';
import { motion } from 'framer-motion';
import { ArrowLeft, Shield, Lock, Users, Activity } from 'lucide-react';
import Navbar from '@/components/Navbar';
import Footer from '@/components/Footer';

export default function DPAPage() {
  return (
    <div className="min-h-screen bg-black text-white font-sans flex flex-col">
      <Navbar />

      <main className="flex-1 pt-40 pb-24 relative overflow-hidden">
        {/* Decorative background gradients */}
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(255,78,0,0.03),transparent)] pointer-events-none" />
        <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-accent/5 blur-[120px] rounded-full pointer-events-none" />

        <div className="max-w-4xl mx-auto px-6 relative z-10">
          {/* Back Button */}
          <Link
            href="/onboarding"
            className="inline-flex items-center gap-2 text-white/50 hover:text-white transition-colors mb-10 group"
          >
            <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
            Back to Onboarding
          </Link>

          {/* Header */}
          <div className="mb-16">
            <h1 className="font-serif text-4xl md:text-5xl font-medium mb-4 leading-tight">
              Data Processing Addendum (DPA)
            </h1>
            <p className="text-white/40 text-sm">Last updated: June 7, 2026</p>
          </div>

          {/* Core Info Icons Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 mb-16">
            <div className="p-6 rounded-2xl bg-white/[0.02] border border-white/5 backdrop-blur-3xl">
              <Users className="w-8 h-8 text-accent mb-4" />
              <h3 className="font-serif text-lg font-medium mb-2">Controller / Processor</h3>
              <p className="text-sm text-white/40 leading-relaxed">
                Clarifies roles: your firm is the Data Controller, Enma Labs is the Data Processor.
              </p>
            </div>
            <div className="p-6 rounded-2xl bg-white/[0.02] border border-white/5 backdrop-blur-3xl">
              <Shield className="w-8 h-8 text-accent mb-4" />
              <h3 className="font-serif text-lg font-medium mb-2">Security Measures</h3>
              <p className="text-sm text-white/40 leading-relaxed">
                We implement industry-grade technical safeguards to prevent unauthorized data access.
              </p>
            </div>
            <div className="p-6 rounded-2xl bg-white/[0.02] border border-white/5 backdrop-blur-3xl">
              <Activity className="w-8 h-8 text-accent mb-4" />
              <h3 className="font-serif text-lg font-medium mb-2">Compliance Assured</h3>
              <p className="text-sm text-white/40 leading-relaxed">
                Enables firms to demonstrate compliance with standard data protection regulations.
              </p>
            </div>
          </div>

          {/* Content */}
          <div className="prose prose-invert max-w-none space-y-10 font-sans text-white/70 leading-[1.7] text-[16px]">
            <section className="space-y-4">
              <h2 className="font-serif text-2xl text-white font-medium mb-4 border-b border-white/5 pb-2">
                1. Scope and Applicability
              </h2>
              <p>
                This Data Processing Addendum ("DPA") governs the processing of personal and business data
                provided by the Chartered Accountant Firm ("Customer" or "Controller") to Enma Labs
                ("Processor") in connection with the Enma automation services. This DPA is integrated into,
                and forms part of, our Terms of Service.
              </p>
            </section>

            <section className="space-y-4">
              <h2 className="font-serif text-2xl text-white font-medium mb-4 border-b border-white/5 pb-2">
                2. Roles and Responsibilities
              </h2>
              <ul className="list-disc pl-6 space-y-3">
                <li>
                  <strong>Controller:</strong> The Customer determines the purposes and means of processing personal
                  data, controls files uploaded to the Telegram bot or dashboard, and ensures the necessary consents
                  are in place before processing begins.
                </li>
                <li>
                  <strong>Processor:</strong> Enma Labs processes personal and business data solely on the Customer's
                  written instructions, including the metadata extraction, OCR processing, and automated auditing
                  requested through the platform dashboard.
                </li>
              </ul>
            </section>

            <section className="space-y-4">
              <h2 className="font-serif text-2xl text-white font-medium mb-4 border-b border-white/5 pb-2">
                3. Processor Obligations
              </h2>
              <p>
                As Processor, Enma Labs agrees to:
              </p>
              <ul className="list-disc pl-6 space-y-3">
                <li>
                  Process data only on documented instructions from the Controller, including with respect to transfer
                  of data outside the originating country.
                </li>
                <li>
                  Ensure that all personnel authorized to process personal data have committed themselves to confidentiality
                  agreements or are under an appropriate statutory obligation of confidentiality.
                </li>
                <li>
                  Implement technical and organizational measures to assist the Controller in responding to requests
                  from data subjects exercising their legal rights (e.g., access, deletion, correction).
                </li>
              </ul>
            </section>

            <section className="space-y-4">
              <h2 className="font-serif text-2xl text-white font-medium mb-4 border-b border-white/5 pb-2">
                4. Security Measures
              </h2>
              <p>
                Enma Labs will maintain appropriate technical and organizational safeguards to protect data from
                accidental loss, alteration, unauthorized disclosure, or access:
              </p>
              <ul className="list-disc pl-6 space-y-3">
                <li>
                  <strong>Pseudonymization & Encryption:</strong> All client records are logically isolated using Postgres
                  Row-Level Security. Database tables and storage buckets are encrypted using AES-256 at rest and HTTPS/TLS
                  in transit.
                </li>
                <li>
                  <strong>System Resilience:</strong> Automated backups, distributed container services, and physical
                  redundancies to ensure continuous service availability.
                </li>
                <li>
                  <strong>Incident Response:</strong> Regular vulnerability scanning and immediate incident logging in
                  our system logs.
                </li>
              </ul>
            </section>

            <section className="space-y-4">
              <h2 className="font-serif text-2xl text-white font-medium mb-4 border-b border-white/5 pb-2">
                5. Sub-processors
              </h2>
              <p>
                Customer authorizes Processor to engage sub-processors to perform infrastructure services necessary
                to deliver the platform. We bind all sub-processors to standard contractual clauses ensuring
                an equivalent level of data protection. Our primary sub-processor list includes:
              </p>
              <table className="w-full text-left border-collapse border border-white/5 mt-4 text-sm text-white/50">
                <thead>
                  <tr className="bg-white/5 text-white">
                    <th className="p-3 border border-white/5 font-serif font-medium">Sub-processor</th>
                    <th className="p-3 border border-white/5 font-serif font-medium">Service Description</th>
                    <th className="p-3 border border-white/5 font-serif font-medium">Location</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="p-3 border border-white/5 font-medium text-white">Supabase Inc.</td>
                    <td className="p-3 border border-white/5">Database Hosting, Auth, and Storage</td>
                    <td className="p-3 border border-white/5">United States (AWS)</td>
                  </tr>
                  <tr>
                    <td className="p-3 border border-white/5 font-medium text-white">Vercel Inc.</td>
                    <td className="p-3 border border-white/5">Frontend Application Hosting & Deployment</td>
                    <td className="p-3 border border-white/5">Global CDN</td>
                  </tr>
                </tbody>
              </table>
            </section>

            <section className="space-y-4">
              <h2 className="font-serif text-2xl text-white font-medium mb-4 border-b border-white/5 pb-2">
                6. Incident Notification & Audit Rights
              </h2>
              <ul className="list-disc pl-6 space-y-3">
                <li>
                  <strong>Breach Notification:</strong> In the event of a verified security incident affecting Controller
                  data, Enma Labs will notify the Customer within 72 hours of detection and provide regular updates.
                </li>
                <li>
                  <strong>Audits:</strong> Enma Labs will provide reasonable compliance summaries, configuration reports,
                  and documentation to allow the Customer to verify compliance with this DPA.
                </li>
              </ul>
            </section>

            <section className="space-y-4">
              <h2 className="font-serif text-2xl text-white font-medium mb-4 border-b border-white/5 pb-2">
                7. Data Return and Deletion
              </h2>
              <p>
                Upon termination of the service agreement, Enma Labs will delete all Customer-provided personal and
                financial documents, along with all associated database records, within 30 days, unless statutory
                storage obligations require longer retention.
              </p>
            </section>
          </div>
        </div>
      </main>

      <Footer />
    </div>
  );
}
