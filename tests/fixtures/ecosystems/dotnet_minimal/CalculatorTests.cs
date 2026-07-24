using Xunit;

namespace DotnetFixture;

public sealed class CalculatorTests
{
    [Fact]
    public void AddsTwoValues()
    {
        Assert.Equal(5, Calculator.Add(2, 3));
    }
}
