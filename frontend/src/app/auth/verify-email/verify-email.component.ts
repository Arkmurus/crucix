import { Component, OnInit } from '@angular/core';
import { FormBuilder, FormGroup, Validators } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';

@Component({
  selector: 'app-verify-email',
  templateUrl: './verify-email.component.html',
  styleUrls: ['./verify-email.component.scss']
})
export class VerifyEmailComponent implements OnInit {
  verifyForm!: FormGroup;
  loading = false;
  resendLoading = false;
  errorMsg = '';
  successMsg = '';
  email = '';

  constructor(
    private fb: FormBuilder,
    private route: ActivatedRoute,
    private router: Router,
    private authService: AuthService
  ) {}

  ngOnInit(): void {
    this.email = this.route.snapshot.queryParamMap.get('email') || '';
    this.verifyForm = this.fb.group({
      code: ['', [Validators.required, Validators.pattern(/^\d{6}$/)]]
    });
  }

  onSubmit(): void {
    if (this.verifyForm.invalid) {
      this.verifyForm.markAllAsTouched();
      return;
    }
    this.loading = true;
    this.errorMsg = '';
    this.successMsg = '';

    const { code } = this.verifyForm.value;

    this.authService.verifyEmail(this.email, code).subscribe({
      next: () => {
        this.loading = false;
        this.successMsg = 'Email verified successfully! Redirecting to sign in...';
        setTimeout(() => {
          this.router.navigate(['/auth/sign-in']);
        }, 2000);
      },
      error: (err) => {
        this.loading = false;
        this.errorMsg = err?.error?.message || 'Verification failed. Please check your code and try again.';
      }
    });
  }

  resendCode(): void {
    if (!this.email) return;
    this.resendLoading = true;
    this.errorMsg = '';
    this.successMsg = '';

    this.authService.resendVerification(this.email).subscribe({
      next: () => {
        this.resendLoading = false;
        this.successMsg = 'A new verification code has been sent to your email.';
      },
      error: (err) => {
        this.resendLoading = false;
        this.errorMsg = err?.error?.message || 'Failed to resend code. Please try again.';
      }
    });
  }
}
