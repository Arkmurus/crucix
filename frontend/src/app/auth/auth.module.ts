import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';

import { AuthRoutingModule } from './auth-routing.module';
import { SignInComponent } from './sign-in/sign-in.component';
import { SignUpComponent } from './sign-up/sign-up.component';
import { ForgotPasswordComponent } from './forgot-password/forgot-password.component';
import { ResetPasswordComponent } from './reset-password/reset-password.component';
import { LockScreenComponent } from './lock-screen/lock-screen.component';
import { SigninWithHeaderFooterComponent } from './signin-with-header-footer/signin-with-header-footer.component';
import { SignupWithHeaderFooterComponent } from './signup-with-header-footer/signup-with-header-footer.component';
import { VerifyEmailComponent } from './verify-email/verify-email.component';

import { MatModule } from 'src/app/appModules/mat.module';
import { CoverForgotPasswordComponent } from './cover-forgot-password/cover-forgot-password.component';
import { CoverResetPasswordComponent } from './cover-reset-password/cover-reset-password.component';
import { CoverSigninComponent } from './cover-signin/cover-signin.component';
import { CoverSignupComponent } from './cover-signup/cover-signup.component';



@NgModule({
  declarations: [
    SignInComponent,
    SignUpComponent,
    ForgotPasswordComponent,
    ResetPasswordComponent,
    LockScreenComponent,
    SigninWithHeaderFooterComponent,
    SignupWithHeaderFooterComponent,
    VerifyEmailComponent,
  ],
  imports: [
    CommonModule,
    ReactiveFormsModule,
    FormsModule,
    RouterModule,
    AuthRoutingModule,
    MatModule,
    CoverSigninComponent,
    CoverSignupComponent,
    CoverForgotPasswordComponent,
    CoverResetPasswordComponent
  ]
})
export class AuthModule { }
