# Run with: gitlab-rails runner /tmp/gitlab_admin_token.rb   (ENV ADMIN_TOKEN must be set)
token_value = ENV.fetch('ADMIN_TOKEN')
root = User.find_by_username('root') or abort('root user not found')

root.personal_access_tokens.where(name: 'ai-review-bootstrap').destroy_all
pat = root.personal_access_tokens.new(
  name: 'ai-review-bootstrap',
  scopes: [:api, :create_runner],
  expires_at: 90.days.from_now.to_date
)
pat.set_token(token_value)
pat.save!
puts "admin token created (id=#{pat.id})"
