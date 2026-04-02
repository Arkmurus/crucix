import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatModule } from 'src/app/appModules/mat.module';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-e505',
  standalone: true,
  imports: [CommonModule, MatModule, RouterModule],
  templateUrl: './e505.component.html',
  styleUrl: './e505.component.scss'
})
export class E505Component {
  currentYear = new Date().getFullYear();
}
