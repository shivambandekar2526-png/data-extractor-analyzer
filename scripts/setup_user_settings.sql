-- =====================================================================
-- Supabase Schema Migration: User Settings & Persistent API Keys
-- =====================================================================
-- Run this in the Supabase SQL Editor to enable Row-Level Security (RLS)
-- segregated user settings and persistent personal API keys.
-- =====================================================================

-- 1. Create table for per-user settings, personal API keys, and model preferences
CREATE TABLE IF NOT EXISTS public.user_settings (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    api_keys JSONB NOT NULL DEFAULT '{}'::jsonb,
    preferences JSONB NOT NULL DEFAULT '{
        "provider": "gemini",
        "model": "gemini-flash-lite-latest",
        "auto_orchestrate": true,
        "routing_mode": "auto_hybrid"
    }'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Enable Row-Level Security
ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;

-- 3. RLS Policies: Users can ONLY read and modify their own settings
DROP POLICY IF EXISTS "Users can view own settings" ON public.user_settings;
CREATE POLICY "Users can view own settings"
    ON public.user_settings FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert own settings" ON public.user_settings;
CREATE POLICY "Users can insert own settings"
    ON public.user_settings FOR INSERT
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update own settings" ON public.user_settings;
CREATE POLICY "Users can update own settings"
    ON public.user_settings FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- 4. Automatic profile initialization trigger upon new user sign up
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.user_settings (user_id, email, api_keys, preferences)
    VALUES (NEW.id, NEW.email, '{}'::jsonb, '{
        "provider": "gemini",
        "model": "gemini-flash-lite-latest",
        "auto_orchestrate": true,
        "routing_mode": "auto_hybrid"
    }'::jsonb)
    ON CONFLICT (user_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
