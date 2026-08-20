import { Suspense } from 'react';
import { SignInForm } from '@/components/signin-form';

// useSearchParams() (in SignInForm) must sit inside a Suspense boundary for prerender.
export default function SignInPage() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6" data-aria-surface="next-signin">
      <Suspense fallback={null}>
        <SignInForm />
      </Suspense>
    </div>
  );
}
