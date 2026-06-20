'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { Check, Loader2, Building2, User, Phone, Shield, Lock } from 'lucide-react';
import { getSupabase } from '@/lib/supabase';
import { useAuth } from '@/lib/auth-context';
import { useRouter } from 'next/navigation';
import { GlassButton } from '@/components/GlassButton';

export default function OnboardingPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [dpaAgreed, setDpaAgreed] = useState(false);
  const [privacyAgreed, setPrivacyAgreed] = useState(false);
  const [dataTrainingConsent, setDataTrainingConsent] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const [formData, setFormData] = useState({
    firmName: '',
    caName: '',
    phone: '',
  });

  // Redirect if not authenticated
  useEffect(() => {
    if (!authLoading && !user) {
      router.push('/login');
    }
  }, [user, authLoading, router]);

  // Check if onboarding is already completed
  useEffect(() => {
    if (!user) return;
    const checkOnboarding = async () => {
      const supabase = getSupabase();
      const { data } = await supabase
        .from('ca_firms')
        .select('onboarding_completed')
        .eq('supabase_user_id', user.id)
        .single();

      if (data?.onboarding_completed) {
        router.push('/dashboard');
      }
    };
    checkOnboarding();
  }, [user, router]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    setError('');

    if (password.length < 8) {
      setError('Password must be at least 8 characters long.');
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setLoading(true);

    try {
      const supabase = getSupabase();

      // Update the user's password credential in Supabase Auth
      const { error: passwordError } = await supabase.auth.updateUser({
        password: password,
      });

      if (passwordError) throw passwordError;

      // Check if a ca_firms row already exists for this user
      const { data: existingFirm } = await supabase
        .from('ca_firms')
        .select('id')
        .eq('supabase_user_id', user.id)
        .single();

      if (existingFirm) {
        // Update existing row
        const { error: updateError } = await supabase
          .from('ca_firms')
          .update({
            firm_name: formData.firmName,
            ca_name: formData.caName,
            phone: formData.phone,
            email: user.email,
            onboarding_completed: true,
            dpa_consented: dpaAgreed,
            dpa_consented_at: new Date().toISOString(),
            privacy_policy_consented: privacyAgreed,
            privacy_policy_consented_at: new Date().toISOString(),
            data_training_consent: dataTrainingConsent,
            last_login: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          })
          .eq('supabase_user_id', user.id);

        if (updateError) throw updateError;
      } else {
        // Create new row
        const { error: insertError } = await supabase
          .from('ca_firms')
          .insert({
            supabase_user_id: user.id,
            email: user.email,
            firm_name: formData.firmName,
            ca_name: formData.caName,
            phone: formData.phone,
            onboarding_completed: true,
            is_active: true,
            subscription_plan: 'trial',
            dpa_consented: dpaAgreed,
            dpa_consented_at: new Date().toISOString(),
            privacy_policy_consented: privacyAgreed,
            privacy_policy_consented_at: new Date().toISOString(),
            data_training_consent: dataTrainingConsent,
            last_login: new Date().toISOString(),
          });

        if (insertError) throw insertError;
      }

      router.push('/dashboard');
    } catch (err: any) {
      setError(err.message || 'Failed to save your information. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  if (authLoading || !user) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-accent animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white font-sans flex flex-col lg:flex-row">
      {/* Left Panel */}
      <div className="lg:w-1/2 p-8 lg:p-20 flex flex-col justify-between relative overflow-hidden bg-black">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_50%,rgba(255,78,0,0.05),transparent)] pointer-events-none" />

        <div className="relative z-10">
          <Link href="/" className="flex items-center gap-2 group mb-24 inline-flex">
            <div className="w-2 h-2 rounded-full bg-accent shadow-[0_0_12px_rgba(255,78,0,0.8)]" />
            <span className="text-white font-bold text-xl tracking-tight">Enma.</span>
          </Link>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
          >
            <h1 className="font-serif text-5xl lg:text-6xl font-medium leading-[1.1] mb-12">
              Almost there! Set up your firm.
            </h1>

            <div className="space-y-8">
              {[
                'DPA signed before any data flows',
                'Zero LLM training on your clients\' data',
                'Secure Telegram connection in under 2 minutes',
              ].map((point, i) => (
                <div key={i} className="flex items-center gap-6">
                  <div className="w-6 h-6 rounded-full bg-accent/10 flex items-center justify-center shrink-0 border border-accent/20">
                    <Check className="w-3.5 h-3.5 text-accent" />
                  </div>
                  <span className="text-white/70 text-lg font-sans">{point}</span>
                </div>
              ))}
            </div>
          </motion.div>
        </div>
      </div>

      {/* Right Panel (Form) */}
      <div className="lg:w-1/2 p-8 lg:p-20 flex items-center justify-center bg-black border-l border-white/5 relative">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_70%_50%,rgba(255,78,0,0.03),transparent)] pointer-events-none" />
        <div className="w-full max-w-md relative z-10">
          <div className="mb-12">
            <h2 className="font-serif text-4xl font-medium mb-3">Complete Your Profile</h2>
            <p className="text-white/50">
              Signed in as <span className="text-accent font-medium">{user.email}</span>
            </p>
          </div>

          {error && (
            <div className="mb-6 p-4 rounded-2xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <div className="relative">
                <Building2 className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/20" />
                <input
                  type="text"
                  name="firmName"
                  placeholder="CA Firm Name"
                  required
                  value={formData.firmName}
                  onChange={handleChange}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white pl-11 pr-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                />
              </div>
            </div>
            <div>
              <div className="relative">
                <User className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/20" />
                <input
                  type="text"
                  name="caName"
                  placeholder="Your Name (CA Name)"
                  required
                  value={formData.caName}
                  onChange={handleChange}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white pl-11 pr-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                />
              </div>
            </div>
            <div>
              <div className="relative">
                <Phone className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/20" />
                <input
                  type="tel"
                  name="phone"
                  placeholder="Phone Number"
                  required
                  value={formData.phone}
                  onChange={handleChange}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white pl-11 pr-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                />
              </div>
            </div>

            <div>
              <div className="relative">
                <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/20" />
                <input
                  type="password"
                  placeholder="Create Login Password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white pl-11 pr-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                />
              </div>
            </div>

            <div>
              <div className="relative">
                <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/20" />
                <input
                  type="password"
                  placeholder="Confirm Login Password"
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white pl-11 pr-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                />
              </div>
            </div>

            {/* Consent Checkboxes */}
            <div className="space-y-4 pt-2">
              {/* DPA Consent */}
              <label className="flex items-start gap-4 cursor-pointer group">
                <div className="relative flex items-center justify-center mt-1">
                  <input
                    type="checkbox"
                    required
                    checked={dpaAgreed}
                    onChange={(e) => setDpaAgreed(e.target.checked)}
                    className="peer appearance-none w-5 h-5 border border-white/20 rounded-lg bg-white/5 checked:bg-accent checked:border-accent transition-all cursor-pointer"
                  />
                  <Check className="w-3.5 h-3.5 text-white absolute pointer-events-none opacity-0 peer-checked:opacity-100 transition-opacity" />
                </div>
                <span className="text-sm text-white/50 group-hover:text-white transition-colors leading-relaxed">
                  I agree to the <span className="text-white font-medium">Data Processing Addendum (DPA)</span>.
                  {' '}<Link href="/dpa" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline inline-flex items-center gap-0.5 ml-1 font-semibold">
                    Read More
                  </Link>
                </span>
              </label>

              {/* Privacy Policy Consent */}
              <label className="flex items-start gap-4 cursor-pointer group">
                <div className="relative flex items-center justify-center mt-1">
                  <input
                    type="checkbox"
                    required
                    checked={privacyAgreed}
                    onChange={(e) => setPrivacyAgreed(e.target.checked)}
                    className="peer appearance-none w-5 h-5 border border-white/20 rounded-lg bg-white/5 checked:bg-accent checked:border-accent transition-all cursor-pointer"
                  />
                  <Check className="w-3.5 h-3.5 text-white absolute pointer-events-none opacity-0 peer-checked:opacity-100 transition-opacity" />
                </div>
                <span className="text-sm text-white/50 group-hover:text-white transition-colors leading-relaxed">
                  I agree to the <span className="text-white font-medium">Privacy Policy</span> and consent to data processing.
                  {' '}<Link href="/privacy" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline inline-flex items-center gap-0.5 ml-1 font-semibold">
                    Read More
                  </Link>
                </span>
              </label>

              {/* AI Model Training Consent (Optional) */}
              <label className="flex items-start gap-4 cursor-pointer group">
                <div className="relative flex items-center justify-center mt-1">
                  <input
                    type="checkbox"
                    checked={dataTrainingConsent}
                    onChange={(e) => setDataTrainingConsent(e.target.checked)}
                    className="peer appearance-none w-5 h-5 border border-white/20 rounded-lg bg-white/5 checked:bg-accent checked:border-accent transition-all cursor-pointer"
                  />
                  <Check className="w-3.5 h-3.5 text-white absolute pointer-events-none opacity-0 peer-checked:opacity-100 transition-opacity" />
                </div>
                <span className="text-sm text-white/50 group-hover:text-white transition-colors leading-relaxed">
                  I consent to the use of anonymized, aggregated data for improving AI models (optional).
                </span>
              </label>
            </div>

            <GlassButton
              type="submit"
              variant="white"
              className="w-full mt-6"
              disabled={!dpaAgreed || !privacyAgreed || loading}
            >
              {loading ? (
                <span className="flex items-center gap-2 justify-center">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Setting Up...
                </span>
              ) : (
                'Complete Setup & Enter Dashboard'
              )}
            </GlassButton>
          </form>
        </div>
      </div>
    </div>
  );
}
