import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { ForgotPasswordComponent } from './forgot-password/forgot-password.component';
import { LockScreenComponent } from './lock-screen/lock-screen.component';
import { ResetPasswordComponent } from './reset-password/reset-password.component';
import { SignInComponent } from './sign-in/sign-in.component';
import { SignUpComponent } from './sign-up/sign-up.component';
import { CoverSigninComponent } from './cover-signin/cover-signin.component';
import { CoverSignupComponent } from './cover-signup/cover-signup.component';
import { CoverForgotPasswordComponent } from './cover-forgot-password/cover-forgot-password.component';
import { CoverResetPasswordComponent } from './cover-reset-password/cover-reset-password.component';
import { VerifyEmailComponent } from './verify-email/verify-email.component';
import { LandingComponent } from './landing/landing.component';

const routes: Routes = [
  {
    path: '',
    children: [
      { path: 'landing', component: LandingComponent, data: { title: 'Welcome' } },
      { path: '', redirectTo: 'landing', pathMatch: 'full' },
      {
        path: 'sign-in',
        component: SignInComponent,
        data: {
          title: 'Sign In'
        }
      },
      {
        path: 'cover-signin',
        component: CoverSigninComponent,
        data: {
          title: 'Cover Sign In'
        }
      },
      {
        path: 'sign-up',
        component: SignUpComponent,
        data: {
          title: 'Sign Up'
        }
      },
      {
        path: 'cover-signup',
        component: CoverSignupComponent,
        data: {
          title: 'Cover Sign Up'
        }
      },
      {
        path: 'forgot-password',
        component: ForgotPasswordComponent,
        data: {
          title: 'Forgot Password'
        }
      },
      {
        path: 'cover-forgot-password',
        component: CoverForgotPasswordComponent,
        data: {
          title: 'Cover Forgot Password'
        }
      },
      {
        path: 'reset-password',
        component: ResetPasswordComponent,
        data: {
          title: 'Reset Password'
        }
      },
      {
        path: 'cover-reset-password',
        component: CoverResetPasswordComponent,
        data: {
          title: 'Cover Reset Password'
        }
      },
      {
        path: 'lock-screen',
        component: LockScreenComponent,
        data: {
          title: 'Lock Screen'
        }
      },
      {
        path: 'verify-email',
        component: VerifyEmailComponent,
        data: {
          title: 'Verify Email'
        }
      },
    ]
  }
];

@NgModule({
  imports: [RouterModule.forChild(routes)],
  exports: [RouterModule]
})
export class AuthRoutingModule { }
