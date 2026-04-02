import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-cover-forgot-password',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './cover-forgot-password.component.html',
  styleUrl: './cover-forgot-password.component.scss'
})
export class CoverForgotPasswordComponent implements OnInit {
  constructor(private router: Router) {}
  ngOnInit(): void { this.router.navigate(['/auth/forgot-password']); }
}
