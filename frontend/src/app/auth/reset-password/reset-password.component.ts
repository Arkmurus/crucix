import { Component, OnInit } from '@angular/core';
import { AbstractControl, FormBuilder, FormGroup, ValidationErrors, Validators } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';

function passwordsMatch(g: AbstractControl): ValidationErrors | null {
  return g.get('password')?.value === g.get('confirmPassword')?.value ? null : { passwordsMismatch: true };
}

@Component({
  selector: 'app-reset-password',
  templateUrl: './reset-password.component.html',
  styleUrls: ['./reset-password.component.scss']
})
export class ResetPasswordComponent implements OnInit {
  form!: FormGroup;
  loading = false;
  errorMsg = '';
  successMsg = '';
  hide = true;
  hideConfirm = true;

  private email = '';
  private code = '';

  constructor(
    private fb: FormBuilder,
    private auth: AuthService,
    private router: Router,
    private route: ActivatedRoute
  ) {}

  ngOnInit(): void {
    this.email = this.route.snapshot.queryParams['email'] || '';
    this.code  = this.route.snapshot.queryParams['code']  || '';
    this.form  = this.fb.group(
      {
        password:        ['', [Validators.required, Validators.minLength(8)]],
        confirmPassword: ['', Validators.required],
      },
      { validators: passwordsMatch }
    );
  }

  onSubmit(): void {
    if (this.form.invalid) { this.form.markAllAsTouched(); return; }
    this.loading = true;
    this.errorMsg = '';
    this.auth.resetPassword(this.email, this.code, this.form.value.password).subscribe({
      next: () => {
        this.loading = false;
        this.successMsg = 'Password reset successfully. Redirecting to sign in...';
        setTimeout(() => this.router.navigate(['/auth/sign-in']), 2000);
      },
      error: (err) => {
        this.loading = false;
        this.errorMsg = err?.error?.message || 'Reset failed. The link may have expired.';
      }
    });
  }
}
